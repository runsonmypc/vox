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

# Minimum total speech duration to consider audio as containing speech (prevents dropping short words)
_MIN_SPEECH_MS = 80
# Minimum RMS energy threshold for 16-bit PCM audio (rejects pure silence/background hiss, preserves soft speech)
_MIN_RMS_ENERGY = 20.0


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

        vad = webrtcvad.Vad(0)  # aggressiveness 0-3 (0 = least aggressive, prevents false negatives on soft speech/consonants)

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


def resolve_input_device(device_spec: int | str | None, channels: int = 1) -> int | None:
    """Resolve an audio device specification to a valid input device index.

    Supports integer indices and case-insensitive device name substrings.
    Validates that the selected device has >= channels input channels.
    If the specified device is invalid or has 0 input channels, logs a warning
    and gracefully falls back to the default input device.
    """
    try:
        devices = sd.query_devices()
    except Exception as e:
        log.warning("Failed to query audio devices: %s", e)
        return None

    if isinstance(device_spec, str):
        spec_lower = device_spec.lower().strip()
        for idx, d in enumerate(devices):
            if d.get("max_input_channels", 0) >= channels:
                if spec_lower in d.get("name", "").lower():
                    return idx
        log.warning(
            "Configured audio input device %r not found among devices with >= %d input channels",
            device_spec, channels,
        )

    elif isinstance(device_spec, int):
        if 0 <= device_spec < len(devices):
            in_ch = devices[device_spec].get("max_input_channels", 0)
            if in_ch >= channels:
                return device_spec
            log.warning(
                "Configured audio device %d (%s) has %d input channels (needs >= %d)",
                device_spec, devices[device_spec].get("name", "Unknown"), in_ch, channels,
            )
        else:
            log.warning("Configured audio device index %d is out of range (0..%d)", device_spec, len(devices) - 1)

    # Fallback to sounddevice default input device
    try:
        default_in = sd.default.device[0]
        if default_in is not None and 0 <= default_in < len(devices):
            if devices[default_in].get("max_input_channels", 0) >= channels:
                if device_spec is not None:
                    log.info("Falling back to default input device %d: %s", default_in, devices[default_in].get("name"))
                return default_in
    except Exception:
        pass

    # Secondary fallback: find first available device with >= channels
    for idx, d in enumerate(devices):
        if d.get("max_input_channels", 0) >= channels:
            log.info("Falling back to first available input device %d: %s", idx, d.get("name"))
            return idx

    return None


class Recorder:
    """Records audio from the default input device into WAV bytes and streams 24kHz PCM chunks.

    Maintains a rolling pre-roll buffer (500ms) to ensure zero startup latency
    and guarantee that speech starting simultaneously with or prior to hotkey
    release is never clipped.
    """

    def __init__(self, config: Config, preroll_ms: int = 500) -> None:
        self._config = config
        self._sample_rate = config.sample_rate
        self._channels = config.channels
        self._device = config.audio_device
        self._max_frames = config.max_recording_seconds * config.sample_rate
        self._chunks: deque[np.ndarray] = deque()
        self._stream: sd.InputStream | None = None
        self._stream_queue: asyncio.Queue[bytes | None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._is_recording = False
        self._resolved_device: int | None = None
        self._last_resolved_spec: int | str | None = None

        # Pre-roll ring buffer (defaults to 500ms: 10 chunks of 50ms)
        self._blocksize = int(self._sample_rate * 0.05)  # 50ms blocks
        preroll_chunks_count = max(1, int((preroll_ms / 1000.0) / 0.05))
        self._preroll_chunks: deque[np.ndarray] = deque(maxlen=preroll_chunks_count)

    def _get_resolved_device(self) -> int | None:
        target_device = self._config.audio_device if getattr(self, "_config", None) is not None else self._device
        if target_device != self._last_resolved_spec or self._resolved_device is None:
            self._resolved_device = resolve_input_device(target_device, channels=self._channels)
            self._last_resolved_spec = target_device
        return self._resolved_device

    def _ensure_stream(self) -> None:
        """Ensure audio input stream is active, creating it if necessary."""
        resolved_device = self._get_resolved_device()
        if self._stream is not None and getattr(self._stream, "active", False):
            return

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        try:
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                device=resolved_device,
                blocksize=self._blocksize,
                callback=self._callback,
            )
            self._stream.start()
            log.info("Audio stream started (device=%s, rate=%d)", resolved_device, self._sample_rate)
        except Exception as e:
            raise AudioError(f"Failed to start audio stream: {e}") from e

    def warmup(self) -> None:
        """Pre-warm device resolution and PortAudio bindings without keeping microphone open."""
        try:
            dev = self._get_resolved_device()
            dummy = sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                device=dev,
                blocksize=self._blocksize,
            )
            dummy.close()
            log.debug("Audio system pre-warmed (microphone closed)")
        except Exception as e:
            log.debug("Warmup stream could not be initialized: %s (will open on demand)", e)

    def reconfigure(self, config: Config) -> None:
        """Reconfigure audio parameters and refresh the stream."""
        self._config = config
        self._sample_rate = config.sample_rate
        self._channels = config.channels
        self._device = config.audio_device
        self._resolved_device = None
        self._last_resolved_spec = None
        self._blocksize = int(self._sample_rate * 0.05)
        self.close()
        self.warmup()

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
        """Start recording audio with zero latency, prepending pre-roll buffer."""
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

        self._ensure_stream()

        self._chunks.clear()
        if self._preroll_chunks:
            # Prepend pre-roll buffer so words spoken during keypress are never lost
            preroll_list = list(self._preroll_chunks)
            self._chunks.extend(preroll_list)
            if self._stream_queue is not None and self._loop is not None and not self._loop.is_closed():
                for pr_chunk in preroll_list:
                    pcm24k = to_pcm24k(pr_chunk, self._sample_rate)
                    try:
                        self._loop.call_soon_threadsafe(self._stream_queue.put_nowait, pcm24k)
                    except RuntimeError:
                        pass
            self._preroll_chunks.clear()

        self._is_recording = True
        log.info("Recording started (pre-roll chunks: %d)", len(self._chunks))

    def stop(self) -> bytes:
        """Stop active recording turn, close microphone stream, and return WAV bytes."""
        self._is_recording = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
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

    def discard(self) -> None:
        """Stop recording turn, close microphone stream, and discard buffered frames."""
        self._is_recording = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._stream_queue is not None and self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._stream_queue.put_nowait, None)
            except RuntimeError:
                pass

        self._chunks.clear()
        self._preroll_chunks.clear()

    def close(self) -> None:
        """Stop and close the underlying audio stream."""
        self.discard()

    @property
    def is_recording(self) -> bool:
        return self._stream is not None and getattr(self._stream, "active", False) and self._is_recording

    def _callback(
        self, indata: np.ndarray, frames: int, time_info: object, status: sd.CallbackFlags
    ) -> None:
        if status:
            log.warning("Audio callback status: %s", status)
        chunk = indata.copy()
        if self._is_recording:
            self._chunks.append(chunk)

            if self._stream_queue is not None and self._loop is not None and not self._loop.is_closed():
                pcm24k = to_pcm24k(chunk, self._sample_rate)
                try:
                    self._loop.call_soon_threadsafe(self._stream_queue.put_nowait, pcm24k)
                except RuntimeError:
                    pass
        else:
            self._preroll_chunks.append(chunk)

    def _to_wav(self, audio: np.ndarray) -> bytes:
        """Convert int16 numpy array to WAV bytes."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()
