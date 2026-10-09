"""Audio recording via sounddevice, plus the WAV conversions the transcribers need."""

from __future__ import annotations

import asyncio
import atexit
import io
import logging
import math
import threading
import time
import warnings
import wave
from collections.abc import AsyncGenerator, Callable, Iterator
from fractions import Fraction
from functools import cache

import numpy as np
import sounddevice as sd
from numpy.lib.stride_tricks import sliding_window_view

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=UserWarning)
    import webrtcvad

from .config import Config
from .errors import AudioError

log = logging.getLogger(__name__)

# Native drivers can deadlock during stop/close. Never wait indefinitely, including on Quit.
_STREAM_CLOSE_TIMEOUT = 2.0
AUDIO_RESTART_NOTICE = "Microphone did not close: quit and reopen Vox Transfer"

# Minimum total speech duration to consider audio as containing speech (prevents dropping short words)
_MIN_SPEECH_MS = 80
# Minimum RMS energy threshold for 16-bit PCM audio (rejects pure silence/background hiss, preserves soft speech)
_MIN_RMS_ENERGY = 20.0
# Samples per block when summing energy, so a long recording never needs a float copy of all of it
_ENERGY_BLOCK = 1 << 20

# WebRTC VAD rates, and the frame length used
_VAD_RATES = (8000, 16000, 32000, 48000)
_VAD_FRAME_MS = 30

# OpenAI accepts uploads up to 25 MB; stay a little under it
UPLOAD_LIMIT_BYTES = 24 * 1024 * 1024
_WAV_HEADER_BYTES = 44

# The live transcription session takes 24 kHz PCM16
STREAM_RATE = 24000

# Resampling low-pass (Kaiser-windowed sinc): flat to 85% of the lower rate's Nyquist
# frequency and about 70 dB down from it on, so nothing above the new band aliases into it
_PASSBAND = 0.85
_STOPBAND_DB = 70.0
# Rate pairs whose exact ratio needs more filter phases than this (11.025 and 22.05 kHz,
# odd rates) use a close approximation; the timing error stays under 0.01%
_MAX_PHASES = 256
# Outputs computed per matrix row, so a whole-number ratio still makes a worthwhile multiply
_MIN_ROW = 32
# Frames converted at a time: a long recording needs only this much extra memory
_BLOCK_FRAMES = 1 << 16


def read_wav(wav_bytes: bytes) -> tuple[np.ndarray, int, int]:
    """Samples (int16, channels interleaved, read-only), sample rate and channel count of a PCM16 WAV.

    When the data chunk comes last, as in Vox's own WAVs, the samples are a view of
    ``wav_bytes`` rather than a copy: an hour at 48 kHz is 346 MB.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM audio")
        rate, channels = wf.getframerate(), wf.getnchannels()
        size = wf.getnframes() * 2 * channels
        start = len(wav_bytes) - size
        if start >= 8 and wav_bytes[start - 8 : start] == b"data" + size.to_bytes(4, "little"):
            return np.frombuffer(wav_bytes, dtype="<i2", offset=start), rate, channels
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


@cache
def _polyphase(src: int, dst: int) -> tuple[int, int, int, int, np.ndarray]:
    """The low-pass filter for ``src`` -> ``dst`` Hz as one matrix: a row of input (``stride``
    new samples plus the context around them) times it gives the next ``matrix.shape[1]`` outputs.

    Output ``n`` lies at input position ``n * down / up``; its taps are the windowed sinc
    evaluated at the distance to each input sample, so a whole-number ratio is plain
    decimation of the filtered signal and any other ratio interpolates it exactly there.
    """
    ratio = Fraction(src, dst).limit_denominator(_MAX_PHASES)
    down, up = ratio.numerator, ratio.denominator
    outputs = up * math.ceil(_MIN_ROW / up)
    nyquist = min(up / down, 1.0) / 2  # of the lower rate, in cycles per input sample
    width = (1 - _PASSBAND) * nyquist
    cutoff = nyquist - width / 2
    half = math.ceil((_STOPBAND_DB - 7.95) / (4 * math.pi * 2.285 * width))  # Kaiser's length estimate
    beta = 0.1102 * (_STOPBAND_DB - 8.7)

    out = np.arange(outputs)
    offset, phase = np.divmod(out * down, up)
    taps = np.arange(2 * half + 1)[:, None]
    tau = phase / up + half - taps  # from each output to each of its taps, in input samples
    window = np.i0(beta * np.sqrt(np.clip(1 - (tau / half) ** 2, 0, None))) / np.i0(beta)
    kernel = np.where(np.abs(tau) <= half, 2 * cutoff * np.sinc(2 * cutoff * tau) * window, 0.0)
    kernel /= kernel.sum(axis=0)  # every phase passes DC at exactly unit gain
    matrix = np.zeros((offset[-1] + 2 * half + 1, outputs), dtype=np.float32)
    matrix[offset + taps, out] = kernel
    matrix.flags.writeable = False  # shared through the cache
    return up, down, half, outputs * down // up, matrix


class Resampler:
    """Band-limited sample-rate conversion of mono audio, fed a block at a time.

    Blocks can be any size and the result matches converting everything at once,
    because the filter's context carries over from one block to the next. Each
    output needs a millisecond or two of input after it, so ``flush()`` returns
    the last few at the end. Beyond its ends the signal holds its first and last value.
    """

    def __init__(self, src: int, dst: int) -> None:
        self._matrix: np.ndarray | None = None
        self._up = self._down = 1
        if src != dst:
            self._up, self._down, self._half, self._stride, self._matrix = _polyphase(src, dst)
        self._pending: np.ndarray | None = None  # input not yet used up, after the context it needs
        self._frames_in = 0
        self._frames_out = 0

    def output_length(self, frames: int) -> int:
        """How many output samples ``frames`` input samples make."""
        return -(-frames * self._up // self._down)

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Take the next block; returns the float32 outputs it completes."""
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        self._frames_in += len(x)
        if self._matrix is None or not len(x):
            return x
        if self._pending is None:
            self._pending = np.full(self._half, x[0], dtype=np.float32)
        out = self._rows(np.concatenate((self._pending, x)))
        self._frames_out += len(out)
        return out

    def flush(self) -> np.ndarray:
        """The outputs still owed once the input has ended."""
        missing = self.output_length(self._frames_in) - self._frames_out
        if self._matrix is None or missing <= 0:
            return np.empty(0, dtype=np.float32)
        rows = -(-missing // self._matrix.shape[1])
        pending = self._pending
        hold = np.full((rows - 1) * self._stride + len(self._matrix) - len(pending), pending[-1], dtype=np.float32)
        out = self._rows(np.concatenate((pending, hold)))[:missing]
        self._frames_out += len(out)
        return out

    def _rows(self, buf: np.ndarray) -> np.ndarray:
        """Filter every whole row in ``buf``; what is left waits for the next block."""
        width, stride = len(self._matrix), self._stride
        rows = (len(buf) - width) // stride + 1 if len(buf) >= width else 0
        self._pending = buf[rows * stride :]
        if not rows:
            return np.empty(0, dtype=np.float32)
        windows = sliding_window_view(buf, width)[: rows * stride : stride]
        return (np.ascontiguousarray(windows) @ self._matrix).ravel()


def _mono(samples: np.ndarray, channels: int) -> np.ndarray:
    """Float32 mono from int16 samples with ``channels`` interleaved."""
    if channels == 1:
        return samples.astype(np.float32).reshape(-1)
    return samples.reshape(-1, channels).mean(axis=1, dtype=np.float32)


def _pcm16(samples: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(samples), -32768, 32767).astype("<i2")


def mono_blocks(samples: np.ndarray, channels: int, resampler: Resampler) -> Iterator[np.ndarray]:
    """``samples`` (int16, ``channels`` interleaved) as mono int16 through ``resampler``, a block
    at a time, so converting a long recording takes only a block's worth of extra memory."""
    step = _BLOCK_FRAMES * channels
    for start in range(0, len(samples), step):
        yield _pcm16(resampler.process(_mono(samples[start : start + step], channels)))
    yield _pcm16(resampler.flush())


def to_16k_mono(samples: np.ndarray, rate: int, channels: int = 1) -> np.ndarray:
    """Downmix and resample to the 16 kHz mono int16 that speech models take."""
    resampler = Resampler(rate, 16000)
    out = np.empty(resampler.output_length(len(samples) // channels), dtype="<i2")
    end = 0
    for block in mono_blocks(samples, channels, resampler):
        out[end : end + len(block)] = block
        end += len(block)
    return out


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


def is_silent(wav_bytes: bytes) -> bool:
    """Whether no second of the audio is louder than the silence threshold: nothing audible in it.

    Stricter than has_speech, whose whole-recording average and VAD can call a long stretch
    with a few quiet words silent. Use it where skipping audio would lose those words for good.
    """
    try:
        samples, rate, channels = read_wav(wav_bytes)
    except (EOFError, ValueError, wave.Error):
        return False
    window = max(1, rate * channels)  # one second
    step = max(window, _ENERGY_BLOCK // window * window)
    floor = _MIN_RMS_ENERGY**2
    for i in range(0, len(samples), step):
        block = samples[i : i + step].astype(np.float32)
        whole = len(block) // window * window
        if whole and (np.square(block[:whole]).reshape(-1, window).mean(axis=1) >= floor).any():
            return False
        tail = block[whole:]
        if len(tail) and float(np.dot(tail, tail)) / len(tail) >= floor:
            return False
    return True


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
    samples, rate, channels = read_wav(wav_bytes)
    if not len(samples):
        return False

    # Energy check (RMS) to quickly reject silence or mic noise floor
    rms = _rms(samples)
    log.debug("Audio RMS energy: %.1f", rms)
    if rms < _MIN_RMS_ENERGY:
        log.debug("Audio RMS energy %.1f below threshold %.1f, treating as silence", rms, _MIN_RMS_ENERGY)
        return False

    vad = webrtcvad.Vad(0)  # aggressiveness 0-3 (0 = least aggressive, prevents false negatives on soft speech/consonants)

    # WebRTC VAD takes 10/20/30 ms frames of mono audio at 8/16/32/48 kHz
    vad_rate = rate if rate in _VAD_RATES else 16000
    frame = vad_rate * _VAD_FRAME_MS // 1000
    speech_frames = 0
    rest = np.empty(0, dtype="<i2")
    # Block by block, so a long recording that starts with speech is never converted in full
    for block in mono_blocks(samples, channels, Resampler(rate, vad_rate)):
        block = np.concatenate((rest, block))
        whole = len(block) - len(block) % frame
        for i in range(0, whole, frame):
            if vad.is_speech(block[i : i + frame].tobytes(), vad_rate):
                speech_frames += 1
                if speech_frames * _VAD_FRAME_MS >= _MIN_SPEECH_MS:
                    return True  # enough; no need to scan the rest of a long recording
        rest = block[whole:]

    log.debug("VAD: %dms speech detected", speech_frames * _VAD_FRAME_MS)
    return False


def match_input_device(spec: str, devices: list[tuple[int, str]]) -> int | None:
    """Index of the device named ``spec`` among ``(index, name)`` pairs, ignoring case.

    An exact name wins, otherwise the first name containing it. The tray uses this too, so the
    device it shows as selected is the one that records.
    """
    needle = spec.lower().strip()
    exact = next((i for i, name in devices if name.lower().strip() == needle), None)
    return exact if exact is not None else next((i for i, name in devices if needle in name.lower()), None)


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
        inputs = [(idx, d.get("name", "")) for idx, d in enumerate(devices)
                  if d.get("max_input_channels", 0) >= channels]
        match = match_input_device(device_spec, inputs)
        if match is not None:
            return match
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
        self._resampler: Resampler | None = None  # to STREAM_RATE, one per streamed recording
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_limit: Callable[[], None] | None = None
        self._is_recording = False
        self._limit_reached = False
        self._resolved_device: int | None = None
        self._last_resolved_spec: int | str | None = None
        self._pending_config: Config | None = None
        self._level_generation: int | None = None
        self.latest_level: tuple[int, float, float] | None = None
        self._meter_failed = False
        self._failure: str | None = None
        self._close_event: threading.Event | None = None
        self._on_recovered: Callable[[], None] | None = None
        # Re-initialising PortAudio must never overlap opening or closing a stream
        self._lock = threading.RLock()
        self._cleanup_lock = threading.Lock()
        self._callback_lock = threading.Lock()

    @property
    def failure(self) -> str | None:
        with self._cleanup_lock:
            return self._failure

    def set_on_recovered(self, fn: Callable[[], None] | None) -> None:
        """Set a callback invoked on the recording's event loop after delayed shutdown."""
        self._on_recovered = fn

    @staticmethod
    def _post_recovery(loop, callback) -> None:
        # Never run application/UI code on the cleanup thread, including during exit.
        if loop is not None and callback is not None:
            try:
                loop.call_soon_threadsafe(callback)
            except RuntimeError:
                pass  # the owning loop has closed

    def _wait_for_cleanup(self) -> None:
        """Call with the lifecycle lock held, before accepting another turn's audio."""
        if (self._close_event is not None and not self._close_event.is_set()
                and not self._close_event.wait(_STREAM_CLOSE_TIMEOUT)):
            raise AudioError(AUDIO_RESTART_NOTICE)
        if self.failure is not None:
            raise AudioError(self.failure)

    def _get_resolved_device(self) -> int | None:
        if self.failure is not None:
            raise AudioError(self.failure)
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
        if self.failure is not None:
            raise AudioError(self.failure)
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
        """Bound native cleanup without putting a stuck driver in asyncio's executor. Call with the lock held."""
        if self._stream is None:
            return
        stream, self._stream = self._stream, None
        finished = threading.Event()
        self._close_event = finished

        delayed = False
        loop, callback = self._loop, self._on_recovered

        def close() -> None:
            try:
                stream.stop()
            except Exception as e:
                log.warning("Error stopping audio stream: %s", e)
            closed = False
            try:
                stream.close()
                closed = True
            except Exception as e:
                log.warning("Error closing audio stream: %s", e)
            # Independent of _lock: its owner may be waiting for us. Publish completion
            # only after restoring the exit handler and failure state.
            with self._cleanup_lock:
                if closed:
                    if delayed:
                        atexit.register(sd._exit_handler)
                        self._failure = None
                        log.info("Microphone stream closed after initial delay; audio system recovered")
                        self._post_recovery(loop, callback)
                else:
                    self._failure = AUDIO_RESTART_NOTICE
                    if not delayed:
                        atexit.unregister(sd._exit_handler)
                finished.set()

        threading.Thread(target=close, name="vox-audio-close", daemon=True).start()
        if not finished.wait(_STREAM_CLOSE_TIMEOUT):
            with self._cleanup_lock:
                # Cleanup may finish between wait() timing out and acquiring this lock.
                if not finished.is_set():
                    delayed = True
                    self._failure = AUDIO_RESTART_NOTICE
                    # Do not re-enter a still-blocked driver at interpreter exit.
                    atexit.unregister(sd._exit_handler)
                    log.warning("Microphone shutdown timed out after %.1fs; captured audio retained. %s",
                                _STREAM_CLOSE_TIMEOUT, AUDIO_RESTART_NOTICE)
        # Drain in-flight Python work before flushing the resampler or taking buffers.
        # Never hold this lock while waiting for native stop (which can invoke a callback).
        with self._callback_lock:
            self._is_recording = False

    def warmup(self) -> None:
        """Pre-warm device resolution and PortAudio bindings without keeping microphone open."""
        if self.failure is not None:
            return
        if self._close_event is not None and not self._close_event.is_set():
            return
        try:
            with self._lock:
                if self._close_event is not None and not self._close_event.is_set():
                    return
                if self._stream is not None or self._is_recording:
                    return
                dev = self._get_resolved_device()
                self._stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=self._channels,
                    dtype="int16",
                    device=dev,
                    blocksize=self._blocksize,
                )
                self._close_stream()
            log.debug("Audio system pre-warmed (microphone closed)")
        except Exception as e:
            log.debug("Warmup stream could not be initialized: %s (will open on demand)", e)

    def reconfigure(self, config: Config) -> None:
        """Apply new audio settings; during a recording they wait until it ends, so it isn't lost."""
        with self._lock:
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
            if self.failure is not None:
                return None
            if self._stream is not None or self._is_recording:
                return None
            if self._close_event is not None and not self._close_event.is_set():
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
        with self._lock:
            if self._is_recording:
                raise AudioError("Recording already started")
            self._wait_for_cleanup()
            # A fresh queue for each recording: the tail and end marker of one that was just stopped or
            # discarded may still be on their way through the loop, and must land in the old queue
            self.set_level_generation(None)
            self._stream_queue = None
            self.get_chunk_queue(loop)

            self._streaming = stream
            self._resampler = Resampler(self._sample_rate, STREAM_RATE) if stream else None
            self._on_limit = on_limit
            self._chunks = []
            self._frames = 0
            self._limit_reached = False
            # Read now: a limit changed mid-recording applies to the next one
            self._max_frames = max(1, self._config.max_recording_seconds) * self._sample_rate
            self._is_recording = True
            try:
                self._ensure_stream()
            except Exception:
                with self._callback_lock:
                    self._is_recording = False
                self._close_stream()
                raise
        log.info("Recording started")

    def stop(self) -> bytes:
        """Stop active recording turn, close microphone stream, and return WAV bytes."""
        # Settings can reload while the worker encodes audio: keep this recording's format.
        with self._lock:
            sample_rate, channels = self._sample_rate, self._channels
            self.set_level_generation(None)
            self._close_stream()
            self._is_recording = False
            self._end_stream_queue()

            chunks, self._chunks = self._chunks, []
        try:
            if not chunks:
                raise AudioError("No audio data recorded")
            audio = np.concatenate(chunks)
            del chunks
            log.debug("Recorded %d frames (%.1fs)", len(audio), len(audio) / sample_rate)
            return pcm16_wav(audio, sample_rate, channels)
        finally:
            self._apply_pending_config()

    def discard(self) -> None:
        """Stop recording turn, close microphone stream, and discard buffered frames."""
        with self._lock:
            self.set_level_generation(None)
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
        with self._lock:
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
        """Queue the last resampled samples and the end marker. Call once the stream is closed."""
        if self._streaming:
            self._streaming = False
            tail = _pcm16(self._resampler.flush()).tobytes()
            if tail:
                self._post(self._stream_queue.put_nowait, tail)
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
        with self._callback_lock:
            self._accept_block(indata, frames, time_info, status)

    def _accept_block(self, indata, frames, time_info, status) -> None:
        if status:
            log.warning("Audio callback status: %s", status)
        room = self._max_frames - self._frames
        if not self._is_recording or room <= 0:
            return
        chunk = indata[:room].copy()
        self._chunks.append(chunk)
        self._frames += len(chunk)
        if self._streaming:
            pcm = _pcm16(self._resampler.process(_mono(chunk, self._channels))).tobytes()
            if pcm:
                self._post(self._stream_queue.put_nowait, pcm)
        if self._frames >= self._max_frames:
            self._limit_reached = True
            if self._on_limit is not None:
                self._post(self._on_limit)
        # Optional feedback runs after all audio delivery. Only one scalar snapshot is retained;
        # no UI calls or additional audio queue are involved.
        generation = self._level_generation
        if generation is not None and not self._meter_failed:
            try:
                level = _rms(chunk.reshape(-1)) / 32768.0 if chunk.size else 0.0
                if not math.isfinite(level):
                    raise ValueError("nonfinite microphone level")
                level = min(1.0, max(0.0, level))
                if generation == self._level_generation:
                    self.latest_level = (generation, time.monotonic(), level)
            except Exception:
                self._meter_failed = True
                self.latest_level = None
                log.warning("Recording overlay metering unavailable; audio capture continues (restart Vox to retry)")

    def set_level_generation(self, generation: int | None) -> None:
        """Opt in for one recording, or detach. A failed meter stays off for this recorder."""
        self._level_generation = generation
        self.latest_level = None
