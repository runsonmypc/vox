"""Audio recording via sounddevice, plus the WAV conversions the transcribers need."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import warnings
import wave
from collections.abc import AsyncGenerator, Callable

import numpy as np
import sounddevice as sd

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
# Samples per block when summing energy, so a long recording never needs a float copy of all of it
_ENERGY_BLOCK = 1 << 20

# OpenAI accepts uploads up to 25 MB; stay a little under it
UPLOAD_LIMIT_BYTES = 24 * 1024 * 1024
_WAV_HEADER_BYTES = 44


def read_wav(wav_bytes: bytes) -> tuple[np.ndarray, int, int]:
    """Samples (int16, channels interleaved), sample rate and channel count of a PCM16 WAV."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM audio")
        rate, channels = wf.getframerate(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm, dtype="<i2"), rate, channels


def pcm16_wav(samples: np.ndarray, rate: int, channels: int = 1) -> bytes:
    """Encode int16 samples (channels interleaved) as a WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(np.ascontiguousarray(samples, dtype="<i2"))
    return buf.getvalue()


def resample(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linearly resample mono audio. Downsampling by a whole factor keeps every nth sample instead."""
    if src == dst or len(samples) == 0:
        return samples
    if src > dst and src % dst == 0:
        return samples[:: src // dst]
    target_len = round(len(samples) * dst / src)
    return np.interp(
        np.arange(target_len) * (src / dst),
        np.arange(len(samples)),
        samples.astype(np.float32),
    )


def to_16k_mono(samples: np.ndarray, rate: int, channels: int = 1) -> np.ndarray:
    """Downmix and resample to the 16 kHz mono int16 that speech models take."""
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1, dtype=np.float32)
    return np.asarray(resample(samples, rate, 16000), dtype="<i2")


def split_at_pauses(samples: np.ndarray, rate: int, max_samples: int) -> list[np.ndarray]:
    """Cut mono audio into pieces of at most ``max_samples``, each ending in the quietest
    300 ms of its last quarter, so a cut lands in a pause rather than inside a word."""
    frame = max(1, rate // 50)  # 20 ms
    parts: list[np.ndarray] = []
    start = 0
    while len(samples) - start > max_samples:
        lo, hi = start + max_samples * 3 // 4, start + max_samples
        n = (hi - lo) // frame
        if n == 0:
            cut = hi
        else:
            seg = samples[lo : lo + n * frame].astype(np.float32)
            energy = np.square(seg).reshape(n, frame).mean(axis=1)
            width = min(15, n)
            sums = np.cumsum(np.concatenate(([0.0], energy)))
            quietest = int(np.argmin(sums[width:] - sums[:-width]))
            cut = lo + (quietest + width // 2) * frame
        parts.append(samples[start:cut])
        start = cut
    parts.append(samples[start:])
    return parts


def upload_wavs(wav_bytes: bytes, max_bytes: int = UPLOAD_LIMIT_BYTES) -> list[bytes]:
    """The recording as 16 kHz mono PCM16 WAVs for upload: one file, or several split at
    pauses when a single one would be larger than ``max_bytes``."""
    samples = to_16k_mono(*read_wav(wav_bytes))
    max_samples = (max_bytes - _WAV_HEADER_BYTES) // 2
    return [pcm16_wav(part, 16000) for part in split_at_pauses(samples, 16000, max_samples)]


def is_digital_silence(wav_bytes: bytes) -> bool:
    """Whether every sample is exactly zero. A real microphone never does that; a denied one does."""
    try:
        samples, _, _ = read_wav(wav_bytes)
    except (EOFError, ValueError, wave.Error):
        return False
    return len(samples) > 0 and not samples.any()


def to_pcm24k(audio: np.ndarray, orig_rate: int = 48000) -> bytes:
    """Convert audio array to 24kHz mono 16-bit little-endian PCM bytes."""
    samples = np.asarray(audio)
    if samples.ndim > 1:
        if samples.shape[1] > 1:
            samples = samples.mean(axis=1).astype(np.int16)
        else:
            samples = samples.squeeze(axis=1)
    return np.asarray(resample(samples, orig_rate, 24000)).astype("<i2").tobytes()


def _rms(samples: np.ndarray) -> float:
    total = 0.0
    for i in range(0, len(samples), _ENERGY_BLOCK):
        block = samples[i : i + _ENERGY_BLOCK].astype(np.float32)
        total += float(np.dot(block, block))
    return (total / len(samples)) ** 0.5


def has_speech(wav_bytes: bytes) -> bool:
    """Check if WAV audio contains speech using WebRTC VAD and energy threshold."""
    try:
        return _has_speech(wav_bytes)
    except Exception:
        log.warning("Speech detection failed; transcribing anyway", exc_info=True)
        return True  # dropping real speech is worse than one wasted request


def _has_speech(wav_bytes: bytes) -> bool:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sample_rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())

    if not pcm:
        return False

    # Energy check (RMS) to quickly reject silence or mic noise floor
    samples = np.frombuffer(pcm, dtype=np.int16)
    rms = _rms(samples)
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
            vad_pcm = np.repeat(samples, 2).astype("<i2").tobytes()
            vad_rate = 48000
        else:
            vad_pcm = np.asarray(resample(samples, sample_rate, 16000)).astype("<i2").tobytes()
            vad_rate = 16000

    frame_ms = 30
    frame_bytes = 2 * vad_rate * frame_ms // 1000  # 16-bit = 2 bytes/sample

    speech_frames = 0
    for i in range(0, len(vad_pcm) - frame_bytes + 1, frame_bytes):
        if vad.is_speech(vad_pcm[i : i + frame_bytes], vad_rate):
            speech_frames += 1
            if speech_frames * frame_ms >= _MIN_SPEECH_MS:
                return True  # enough; no need to scan the rest of a long recording

    log.debug("VAD: %dms speech detected", speech_frames * frame_ms)
    return False


def resolve_input_device(device_spec: int | str | None, channels: int = 1) -> int | None:
    """Resolve an audio device specification to a valid input device index.

    Supports integer indices and case-insensitive device names: an exact name
    wins, otherwise the first name containing the text. Validates that the
    selected device has >= channels input channels. If the specified device is
    invalid or has 0 input channels, logs a warning and gracefully falls back
    to the default input device.
    """
    try:
        devices = sd.query_devices()
    except Exception as e:
        log.warning("Failed to query audio devices: %s", e)
        return None

    if isinstance(device_spec, str):
        spec_lower = device_spec.lower().strip()
        inputs = [(idx, d.get("name", "").lower()) for idx, d in enumerate(devices)
                  if d.get("max_input_channels", 0) >= channels]
        for idx, name in inputs:
            if name == spec_lower:
                return idx
        for idx, name in inputs:
            if spec_lower in name:
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
    """Records the microphone into WAV bytes and, for live transcription, streams 24 kHz PCM chunks.

    The microphone is open only while recording, so the OS in-use indicator
    means Vox is listening. A recording stops buffering at
    ``max_recording_seconds`` (read when it starts) and reports that through
    ``on_limit``, which bounds memory.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._sample_rate = config.sample_rate
        self._channels = config.channels
        self._blocksize = int(self._sample_rate * 0.05)  # 50ms blocks
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._max_frames = 0
        self._stream: sd.InputStream | None = None
        self._stream_queue: asyncio.Queue[bytes | None] | None = None
        self._streaming = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_limit: Callable[[], None] | None = None
        self._is_recording = False
        self._limit_reached = False
        self._resolved_device: int | None = None
        self._last_resolved_spec: int | str | None = None
        self._pending_config: Config | None = None
        # Re-initialising PortAudio must never overlap opening or closing a stream
        self._lock = threading.Lock()

    def _get_resolved_device(self) -> int | None:
        target_device = self._config.audio_device
        if target_device != self._last_resolved_spec or self._resolved_device is None:
            self._resolved_device = resolve_input_device(target_device, channels=self._channels)
            self._last_resolved_spec = target_device
        return self._resolved_device

    def _ensure_stream(self) -> None:
        """Ensure audio input stream is active, creating it if necessary. Call with the lock held."""
        resolved_device = self._get_resolved_device()
        if self._stream is not None and getattr(self._stream, "active", False):
            return

        self._close_stream()
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

    def _close_stream(self) -> None:
        """Stop and close the stream; stop() returns once the last callback has run. Call with the lock held."""
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as e:
            log.debug("Error closing audio stream: %s", e)
        self._stream = None

    def warmup(self) -> None:
        """Pre-warm device resolution and PortAudio bindings without keeping microphone open."""
        try:
            with self._lock:
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
        """Apply new audio settings; during a recording they wait until it ends, so it isn't lost."""
        if self._is_recording:
            self._pending_config = config
            return
        self._pending_config = None
        self._config = config
        self._sample_rate = config.sample_rate
        self._channels = config.channels
        self._resolved_device = None
        self._last_resolved_spec = None
        self._blocksize = int(self._sample_rate * 0.05)
        self.close()
        self.warmup()

    def refresh_input_devices(self) -> list[tuple[int, str]] | None:
        """Re-scan the audio devices and list the inputs as (index, name).

        PortAudio only enumerates devices when it initialises, so this restarts
        it. That would close any open stream, so it returns None instead while
        one is open.
        """
        with self._lock:
            if self._stream is not None or self._is_recording:
                return None
            sd.stop()  # close sounddevice's play() stream properly; the restart would leave it dangling
            if sd._initialized:
                sd._terminate()
            sd._initialize()
            self._resolved_device = None  # indices can move when devices come and go
            devices = sd.query_devices()
        return [
            (i, d.get("name", f"Device {i}"))
            for i, d in enumerate(devices)
            if d.get("max_input_channels", 0) >= self._channels
        ]

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

    def start(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        stream: bool = False,
        on_limit: Callable[[], None] | None = None,
    ) -> None:
        """Open the microphone and start recording.

        ``stream`` also feeds 24 kHz chunks to ``stream_chunks()``. ``on_limit``
        is called on ``loop`` once the recording reaches max_recording_seconds.
        """
        self.get_chunk_queue(loop)
        self._drop_queued_chunks()

        self._streaming = stream
        self._on_limit = on_limit
        self._chunks = []
        self._frames = 0
        self._limit_reached = False
        # Read now: a limit changed mid-recording applies to the next one
        self._max_frames = max(1, self._config.max_recording_seconds) * self._sample_rate
        self._is_recording = True
        try:
            with self._lock:
                self._ensure_stream()
        except Exception:
            self._is_recording = False
            raise
        log.info("Recording started")

    def stop(self) -> bytes:
        """Stop active recording turn, close microphone stream, and return WAV bytes."""
        with self._lock:
            self._close_stream()
        self._is_recording = False
        self._end_stream_queue()

        chunks, self._chunks = self._chunks, []
        try:
            if not chunks:
                raise AudioError("No audio data recorded")
            audio = np.concatenate(chunks)
            del chunks
            log.debug("Recorded %d frames (%.1fs)", len(audio), len(audio) / self._sample_rate)
            return self._to_wav(audio)
        finally:
            self._apply_pending_config()

    def discard(self) -> None:
        """Stop recording turn, close microphone stream, and discard buffered frames."""
        with self._lock:
            self._close_stream()
        self._is_recording = False
        self._end_stream_queue()
        self._chunks = []
        self._apply_pending_config()

    def close(self) -> None:
        """Stop and close the underlying audio stream."""
        self.discard()

    @property
    def is_recording(self) -> bool:
        return self._stream is not None and getattr(self._stream, "active", False) and self._is_recording

    @property
    def limit_reached(self) -> bool:
        """Whether the current or last recording hit max_recording_seconds."""
        return self._limit_reached

    def _apply_pending_config(self) -> None:
        if self._pending_config is not None:
            self.reconfigure(self._pending_config)

    def stop_streaming(self) -> None:
        """The live session ended before the recording: stop queueing chunks it will never read."""
        self._streaming = False
        self._drop_queued_chunks()

    def _drop_queued_chunks(self) -> None:
        queue = self._stream_queue
        while queue is not None and not queue.empty():
            queue.get_nowait()

    def _end_stream_queue(self) -> None:
        if self._streaming:
            self._post(self._stream_queue.put_nowait, None)

    def _post(self, fn: Callable[..., None], *args: object) -> None:
        """Run ``fn`` on the event loop; the audio callback runs on PortAudio's thread."""
        if self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(fn, *args)
            except RuntimeError:
                pass  # the loop closed in between

    def _callback(
        self, indata: np.ndarray, frames: int, time_info: object, status: sd.CallbackFlags
    ) -> None:
        if status:
            log.warning("Audio callback status: %s", status)
        room = self._max_frames - self._frames
        if not self._is_recording or room <= 0:
            return
        chunk = indata[:room].copy()
        self._chunks.append(chunk)
        self._frames += len(chunk)
        if self._streaming:
            self._post(self._stream_queue.put_nowait, to_pcm24k(chunk, self._sample_rate))
        if self._frames >= self._max_frames:
            self._limit_reached = True
            if self._on_limit is not None:
                self._post(self._on_limit)

    def _to_wav(self, audio: np.ndarray) -> bytes:
        """Convert int16 numpy array to WAV bytes."""
        return pcm16_wav(audio, self._sample_rate, self._channels)
