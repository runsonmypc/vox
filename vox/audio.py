"""Audio recording via sounddevice — captures to in-memory WAV."""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from collections import deque
from typing import AsyncGenerator

import numpy as np
import sounddevice as sd
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=UserWarning)
    import webrtcvad

from .config import Config
from .errors import AudioError

log = logging.getLogger(__name__)

# Minimum total speech duration to consider audio as containing speech
_MIN_SPEECH_MS = 400
# Minimum RMS energy threshold for 16-bit PCM audio (rejects pure silence/background hiss)
_MIN_RMS_ENERGY = 120.0


def to_pcm24k(audio: np.ndarray, orig_rate: int = 48000) -> bytes:
    """Convert audio array to 24kHz mono 16-bit little-endian PCM bytes."""
    samples = np.asarray(audio)
    if samples.ndim > 1:
        if samples.shape[1] > 1:
            samples = samples.mean(axis=1).astype(np.int16)
        else:
            samples = samples.squeeze(axis=1)

    if orig_rate == 24000:
        pcm24k = samples
    elif orig_rate == 48000:
        # Decimate by 2: integer slicing
        pcm24k = samples[::2]
    else:
        # General resampling to 24kHz
        target_len = int(round(len(samples) * 24000 / orig_rate))
        if target_len == 0:
            return b""
        pcm24k = np.interp(
            np.linspace(0, len(samples), target_len, endpoint=False),
            np.arange(len(samples)),
            samples.astype(np.float32),
        ).astype(np.int16)

    # Output 16-bit signed integer little-endian (<i2) bytes
    return pcm24k.astype("<i2").tobytes()


def has_speech(wav_bytes: bytes) -> bool:
    """Check if WAV audio contains speech using WebRTC VAD and energy threshold."""
    try:
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            sample_rate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())

        if not pcm:
            return False

        # Energy check (RMS) to quickly reject silence or mic noise floor
        samples = np.frombuffer(pcm, dtype=np.int16)
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        log.debug("Audio RMS energy: %.1f", rms)
        if rms < _MIN_RMS_ENERGY:
            log.debug("Audio RMS energy %.1f below threshold %.1f, treating as silence", rms, _MIN_RMS_ENERGY)
            return False

        vad = webrtcvad.Vad(2)  # aggressiveness 0-3 (2 = moderate, filters room hiss and breath)

        # WebRTC VAD needs 10/20/30ms frames at 8/16/32/48kHz
        vad_rate = sample_rate
        vad_pcm = pcm
        if sample_rate not in (8000, 16000, 32000, 48000):
            if sample_rate == 24000:
                vad_samples = np.repeat(samples, 2)
                vad_rate = 48000
                vad_pcm = vad_samples.astype("<i2").tobytes()
            else:
                target_len = int(round(len(samples) * 16000 / sample_rate))
                vad_samples = np.interp(
                    np.linspace(0, len(samples), target_len, endpoint=False),
                    np.arange(len(samples)),
                    samples.astype(np.float32),
                ).astype(np.int16)
                vad_rate = 16000
                vad_pcm = vad_samples.astype("<i2").tobytes()

        frame_ms = 30
        frame_bytes = 2 * vad_rate * frame_ms // 1000  # 16-bit = 2 bytes/sample

        speech_frames = 0
        total_frames = 0
        for i in range(0, len(vad_pcm) - frame_bytes + 1, frame_bytes):
            frame = vad_pcm[i : i + frame_bytes]
            total_frames += 1
            if vad.is_speech(frame, vad_rate):
                speech_frames += 1

        speech_ms = speech_frames * frame_ms
        log.debug("VAD: %dms speech detected across %d frames", speech_ms, total_frames)
        return speech_ms >= _MIN_SPEECH_MS
    except Exception as e:
        log.debug("VAD check failed: %s", e)
        return True  # assume speech on error to avoid dropping valid audio


class Recorder:
    """Records audio from the default input device into WAV bytes and streams 24kHz PCM chunks."""

    def __init__(self, config: Config) -> None:
        self._sample_rate = config.sample_rate
        self._channels = config.channels
        self._device = config.audio_device
        self._max_frames = config.max_recording_seconds * config.sample_rate
        self._chunks: deque[np.ndarray] = deque()
        self._stream: sd.InputStream | None = None
        self._stream_queue: asyncio.Queue[bytes | None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def get_chunk_queue(self, loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Queue[bytes | None]:
        """Get or initialize the asyncio Queue that receives 24kHz PCM16 chunks."""
        if loop is not None:
            self._loop = loop
        elif self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        if self._stream_queue is None:
            self._stream_queue = asyncio.Queue()
        return self._stream_queue

    async def stream_chunks(self) -> AsyncGenerator[bytes, None]:
        """Asynchronous generator yielding 24kHz PCM16 chunks during recording."""
        queue = self.get_chunk_queue()
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start recording audio."""
        self._chunks.clear()
        if loop is not None:
            self._loop = loop
        elif self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        if self._stream_queue is None:
            self._stream_queue = asyncio.Queue()
        else:
            while not self._stream_queue.empty():
                try:
                    self._stream_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

        blocksize = int(self._sample_rate * 0.1)
        try:
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                device=self._device,
                blocksize=blocksize,
                callback=self._callback,
            )
            self._stream.start()
            log.info("Recording started (device=%s, rate=%d)", self._device, self._sample_rate)
        except Exception as e:
            raise AudioError(f"Failed to start recording: {e}") from e

    def stop(self) -> bytes:
        """Stop recording, finalize streaming queue, and return complete turn WAV bytes."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if self._stream_queue is not None and self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._stream_queue.put_nowait, None)
            except RuntimeError:
                pass

        if not self._chunks:
            raise AudioError("No audio data recorded")

        audio = np.concatenate(list(self._chunks))
        # Enforce max length
        if len(audio) > self._max_frames:
            log.warning(
                "Recording truncated from %.1fs to %ds (max_recording_seconds limit)",
                len(audio) / self._sample_rate,
                self._max_frames // self._sample_rate,
            )
            audio = audio[: self._max_frames]

        log.debug("Recorded %d frames (%.1fs)", len(audio), len(audio) / self._sample_rate)
        return self._to_wav(audio)

    @property
    def is_recording(self) -> bool:
        return self._stream is not None and self._stream.active

    def _callback(
        self, indata: np.ndarray, frames: int, time_info: object, status: sd.CallbackFlags
    ) -> None:
        if status:
            log.warning("Audio callback status: %s", status)
        chunk = indata.copy()
        self._chunks.append(chunk)

        if self._stream_queue is not None and self._loop is not None and not self._loop.is_closed():
            pcm24k = to_pcm24k(chunk, self._sample_rate)
            try:
                self._loop.call_soon_threadsafe(self._stream_queue.put_nowait, pcm24k)
            except RuntimeError:
                pass

    def _to_wav(self, audio: np.ndarray) -> bytes:
        """Convert int16 numpy array to WAV bytes."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()
