"""Audio feedback — play start/stop/error sounds."""

from __future__ import annotations

import logging
import struct
import math
from pathlib import Path

import numpy as np
import sounddevice as sd

from .config import Config

log = logging.getLogger(__name__)

_SOUNDS_DIR = Path(__file__).parent.parent / "sounds"


def _generate_tone(freq: float, duration: float, sample_rate: int = 44100, volume: float = 0.3) -> np.ndarray:
    """Generate a sine wave tone."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    # Apply fade in/out to avoid clicks
    fade_len = int(sample_rate * 0.01)
    envelope = np.ones_like(t)
    envelope[:fade_len] = np.linspace(0, 1, fade_len)
    envelope[-fade_len:] = np.linspace(1, 0, fade_len)
    return (np.sin(2 * np.pi * freq * t) * volume * envelope).astype(np.float32)


def _start_sound() -> np.ndarray:
    """Rising two-tone chirp."""
    sr = 44100
    t1 = _generate_tone(800, 0.08, sr, 0.25)
    t2 = _generate_tone(1200, 0.08, sr, 0.25)
    gap = np.zeros(int(sr * 0.02), dtype=np.float32)
    return np.concatenate([t1, gap, t2])


def _stop_sound() -> np.ndarray:
    """Falling two-tone chirp."""
    sr = 44100
    t1 = _generate_tone(1200, 0.08, sr, 0.25)
    t2 = _generate_tone(800, 0.08, sr, 0.25)
    gap = np.zeros(int(sr * 0.02), dtype=np.float32)
    return np.concatenate([t1, gap, t2])


def _error_sound() -> np.ndarray:
    """Low buzz."""
    sr = 44100
    t1 = _generate_tone(300, 0.15, sr, 0.3)
    gap = np.zeros(int(sr * 0.05), dtype=np.float32)
    t2 = _generate_tone(200, 0.2, sr, 0.3)
    return np.concatenate([t1, gap, t2])


class SoundPlayer:
    """Plays audio feedback sounds."""

    def __init__(self, config: Config) -> None:
        self._enabled = config.sounds_enabled
        self._sounds: dict[str, np.ndarray] = {}
        if self._enabled:
            self._sounds = {
                "start": _start_sound(),
                "stop": _stop_sound(),
                "error": _error_sound(),
            }

    def play(self, name: str) -> None:
        """Play a named sound (non-blocking)."""
        if not self._enabled:
            return
        sound = self._sounds.get(name)
        if sound is None:
            log.warning("Unknown sound: %s", name)
            return
        try:
            sd.play(sound, samplerate=44100)
        except Exception as e:
            log.warning("Failed to play sound %r: %s", name, e)
