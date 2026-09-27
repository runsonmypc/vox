"""Audio feedback: start/stop/error and the other cues, each replaceable with a WAV file of your own."""

from __future__ import annotations

import logging
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from .config import DEFAULT_CONFIG_PATH, Config

log = logging.getLogger(__name__)

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


def sounds_dir(config: Config) -> Path:
    """Where custom cues go: sounds/ next to config.toml, so ~/.config/vox/sounds/start.wav replaces "start"."""
    return (config.config_path or DEFAULT_CONFIG_PATH).parent / "sounds"


def _custom_files(directory: Path) -> dict[str, Path]:
    """The cues that have a WAV file of their own in ``directory``."""
    files = {name: directory / f"{name}.wav" for name in _SYSTEM_SOUNDS}
    custom = {name: path for name, path in files.items() if path.is_file()}
    for name, path in custom.items():
        log.info("Using %s for the %s sound", path, name)
    return custom


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


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    """A 16-bit PCM WAV file as float32 frames for sounddevice, and its sample rate."""
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"{8 * wf.getsampwidth()}-bit audio; Vox plays 16-bit PCM")
        rate, channels = wf.getframerate(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    frames = np.frombuffer(pcm, dtype="<i2").reshape(-1, channels)
    return frames.astype(np.float32) / 32768, rate


def _macos_sounds(custom: dict[str, Path]) -> dict:
    from AppKit import NSSound

    sounds = {}
    for name, system_name in _SYSTEM_SOUNDS.items():
        sound = None
        if name in custom:
            sound = NSSound.alloc().initWithContentsOfFile_byReference_(str(custom[name]), True)
            if sound is None:
                log.warning("Couldn't load %s; using the built-in %s sound", custom[name], name)
        if sound is None:
            sound = NSSound.soundNamed_(system_name)
        if sound is None and name == "cancel":
            sound = NSSound.soundNamed_("Purr")
        sounds[name] = sound
    return sounds


def _tone_sounds(custom: dict[str, Path]) -> dict[str, tuple[np.ndarray, int]]:
    sounds = {}
    for name, spec in _TONES.items():
        if name in custom:
            try:
                sounds[name] = _read_wav(custom[name])
                continue
            except (OSError, EOFError, ValueError, wave.Error) as e:
                log.warning("Couldn't load %s (%s); using the built-in %s sound", custom[name], e, name)
        sounds[name] = (_two_tone(*spec), _SAMPLE_RATE)
    return sounds


class SoundPlayer:
    """Plays audio feedback sounds while config.sounds_enabled is on.

    The sound table is always built, so turning sounds on in a reloaded config works at once.
    Custom WAV files are read once, at startup.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._is_darwin = sys.platform == "darwin"
        custom = _custom_files(sounds_dir(config))
        self._sounds = {}
        if self._is_darwin:
            try:
                self._sounds = _macos_sounds(custom)
            except Exception as e:
                log.warning("Failed to initialize macOS NSSound: %s", e)
                self._is_darwin = False
        if not self._is_darwin:
            self._sounds = _tone_sounds(custom)

    def play(self, name: str) -> None:
        """Start playing a named sound; it plays on while the caller carries on."""
        if not self._config.sounds_enabled:
            return
        sound = self._sounds.get(name)
        if sound is None:
            log.warning("Unknown sound: %s", name)
            return
        try:
            if self._is_darwin:
                sound.stop()
                sound.play()
            else:
                samples, rate = sound
                sd.play(samples, samplerate=rate)
        except Exception as e:
            log.warning("Failed to play sound %r: %s", name, e)
