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


def _busy_sound() -> np.ndarray:
    """Quick double-tap tone."""
    sr = 44100
    tap = _generate_tone(600, 0.05, sr, 0.2)
    gap = np.zeros(int(sr * 0.04), dtype=np.float32)
    return np.concatenate([tap, gap, tap])


def _cancel_sound() -> np.ndarray:
    """Descending two-tone sweep."""
    sr = 44100
    t1 = _generate_tone(900, 0.06, sr, 0.25)
    gap = np.zeros(int(sr * 0.02), dtype=np.float32)
    t2 = _generate_tone(450, 0.08, sr, 0.25)
    return np.concatenate([t1, gap, t2])


def _pause_sound() -> np.ndarray:
    """Low falling pair: dictation paused."""
    sr = 44100
    t1 = _generate_tone(660, 0.07, sr, 0.22)
    gap = np.zeros(int(sr * 0.03), dtype=np.float32)
    t2 = _generate_tone(440, 0.1, sr, 0.22)
    return np.concatenate([t1, gap, t2])


def _resume_sound() -> np.ndarray:
    """Low rising pair: dictation resumed."""
    sr = 44100
    t1 = _generate_tone(440, 0.07, sr, 0.22)
    gap = np.zeros(int(sr * 0.03), dtype=np.float32)
    t2 = _generate_tone(660, 0.1, sr, 0.22)
    return np.concatenate([t1, gap, t2])


class SoundPlayer:
    """Plays audio feedback sounds."""

    def __init__(self, config: Config) -> None:
        self._enabled = config.sounds_enabled
        self._is_darwin = sys.platform == "darwin"
        self._sounds = {}
        if self._enabled:
            if self._is_darwin:
                try:
                    from AppKit import NSSound
                    # Map to crisp, responsive macOS system alert sounds
                    system_defaults = {
                        "start": "Tink",
                        "stop": "Pop",
                        "error": "Basso",
                        "busy": "Funk",
                        "cancel": "Blow",
                        "pause": "Bottle",
                        "resume": "Glass",
                    }
                    for name, system_name in system_defaults.items():
                        wav_path = _SOUNDS_DIR / f"{name}.wav"
                        if wav_path.exists():
                            self._sounds[name] = NSSound.alloc().initWithContentsOfFile_byReference_(str(wav_path), True)
                        else:
                            sound = NSSound.soundNamed_(system_name)
                            if sound is None and name == "cancel":
                                sound = NSSound.soundNamed_("Purr")
                            self._sounds[name] = sound
                except Exception as e:
                    log.warning("Failed to initialize macOS NSSound: %s", e)
                    self._is_darwin = False

            if not self._is_darwin:
                self._sounds = {
                    "start": _start_sound(),
                    "stop": _stop_sound(),
                    "error": _error_sound(),
                    "busy": _busy_sound(),
                    "cancel": _cancel_sound(),
                    "pause": _pause_sound(),
                    "resume": _resume_sound(),
                }

    def play(self, name: str, blocking: bool = False) -> None:
        """Play a named sound. If blocking=True, wait for it to finish."""
        if not self._enabled:
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
                return
            except Exception as e:
                log.debug("NSSound play failed: %s, falling back to sounddevice", e)

        try:
            sd.play(sound, samplerate=44100)
            if blocking:
                sd.wait()
        except Exception as e:
            log.warning("Failed to play sound %r: %s", name, e)
