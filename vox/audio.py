"""Audio recording via sounddevice — captures to in-memory WAV."""

from __future__ import annotations

import io
import logging
import wave
from collections import deque

import numpy as np
import sounddevice as sd

from .config import Config
from .errors import AudioError

log = logging.getLogger(__name__)


class Recorder:
    """Records audio from the default input device into WAV bytes."""

    def __init__(self, config: Config) -> None:
        self._sample_rate = config.sample_rate
        self._channels = config.channels
        self._device = config.audio_device
        self._max_frames = config.max_recording_seconds * config.sample_rate
        self._chunks: deque[np.ndarray] = deque()
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        """Start recording audio."""
        self._chunks.clear()
        try:
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                device=self._device,
                callback=self._callback,
            )
            self._stream.start()
            log.debug("Recording started (device=%s, rate=%d)", self._device, self._sample_rate)
        except Exception as e:
            raise AudioError(f"Failed to start recording: {e}") from e

    def stop(self) -> bytes:
        """Stop recording and return WAV bytes."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._chunks:
            raise AudioError("No audio data recorded")

        audio = np.concatenate(list(self._chunks))
        # Enforce max length
        if len(audio) > self._max_frames:
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
        self._chunks.append(indata.copy())

    def _to_wav(self, audio: np.ndarray) -> bytes:
        """Convert int16 numpy array to WAV bytes."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()
