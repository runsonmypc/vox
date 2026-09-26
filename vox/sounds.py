"""Audio feedback — play start/stop/error sounds."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from .config import Config

log = logging.getLogger(__name__)

_SOUNDS_DIR = Path(__file__).parent.parent / "sounds"

_SAMPLE_RATE = 44100

# macOS: crisp, responsive system alert sounds
_SYSTEM_SOUNDS = {
    "start": "Tink",
    "stop": "Pop",
    "error": "Basso",
    "busy": "Funk",
    "cancel": "Blow",
    "pause": "Bottle",
    "resume": "Glass",
}

# Elsewhere: two tones with a gap, as ((freq, seconds, volume), gap seconds, (freq, seconds, volume))
_TONES = {
    "start": ((800, 0.08, 0.25), 0.02, (1200, 0.08, 0.25)),  # rising chirp
    "stop": ((1200, 0.08, 0.25), 0.02, (800, 0.08, 0.25)),  # falling chirp
    "error": ((300, 0.15, 0.3), 0.05, (200, 0.2, 0.3)),  # low buzz
    "busy": ((600, 0.05, 0.2), 0.04, (600, 0.05, 0.2)),  # quick double tap
    "cancel": ((900, 0.06, 0.25), 0.02, (450, 0.08, 0.25)),  # descending sweep
    "pause": ((660, 0.07, 0.22), 0.03, (440, 0.1, 0.22)),  # low falling pair
    "resume": ((440, 0.07, 0.22), 0.03, (660, 0.1, 0.22)),  # low rising pair
}


def _generate_tone(freq: float, duration: float, sample_rate: int = _SAMPLE_RATE, volume: float = 0.3) -> np.ndarray:
    """Generate a sine wave tone."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    # Apply fade in/out to avoid clicks
    fade_len = int(sample_rate * 0.01)
    envelope = np.ones_like(t)
    envelope[:fade_len] = np.linspace(0, 1, fade_len)
    envelope[-fade_len:] = np.linspace(1, 0, fade_len)
    return (np.sin(2 * np.pi * freq * t) * volume * envelope).astype(np.float32)


def _two_tone(first: tuple[float, float, float], gap: float, second: tuple[float, float, float]) -> np.ndarray:
    (freq1, dur1, vol1), (freq2, dur2, vol2) = first, second
    return np.concatenate([
        _generate_tone(freq1, dur1, _SAMPLE_RATE, vol1),
        np.zeros(int(_SAMPLE_RATE * gap), dtype=np.float32),
        _generate_tone(freq2, dur2, _SAMPLE_RATE, vol2),
    ])


def _macos_sounds() -> dict:
    from AppKit import NSSound

    sounds = {}
    for name, system_name in _SYSTEM_SOUNDS.items():
        wav_path = _SOUNDS_DIR / f"{name}.wav"
        if wav_path.exists():
            sounds[name] = NSSound.alloc().initWithContentsOfFile_byReference_(str(wav_path), True)
        else:
            sound = NSSound.soundNamed_(system_name)
            if sound is None and name == "cancel":
                sound = NSSound.soundNamed_("Purr")
            sounds[name] = sound
    return sounds


class SoundPlayer:
    """Plays audio feedback sounds while config.sounds_enabled is on.

    The sound table is always built, so turning sounds on in a reloaded config works at once.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._is_darwin = sys.platform == "darwin"
        self._sounds = {}
        if self._is_darwin:
            try:
                self._sounds = _macos_sounds()
            except Exception as e:
                log.warning("Failed to initialize macOS NSSound: %s", e)
                self._is_darwin = False
        if not self._is_darwin:
            self._sounds = {name: _two_tone(*spec) for name, spec in _TONES.items()}

    def play(self, name: str, blocking: bool = False) -> None:
        """Play a named sound. If blocking=True, wait for it to finish."""
        if not self._config.sounds_enabled:
            return
        sound = self._sounds.get(name)
        if sound is None:
            log.warning("Unknown sound: %s", name)
            return

        if self._is_darwin:
            try:
                sound.stop()
                sound.play()
                if blocking:
                    time.sleep(0.08)  # brief pause so alert finishes before microphone starts
            except Exception as e:
                log.warning("Failed to play sound %r: %s", name, e)
            return

        try:
            sd.play(sound, samplerate=_SAMPLE_RATE)
            if blocking:
                sd.wait()
        except Exception as e:
            log.warning("Failed to play sound %r: %s", name, e)
