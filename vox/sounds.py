"""Audio feedback: start/stop/error and the other cues, each replaceable with a WAV file of your own."""

from __future__ import annotations

import logging
import sys
import time
import wave
from pathlib import Path
from typing import NamedTuple

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


class Partial(NamedTuple):
    """One sine, or band of noise, in a built-in sound.

    It rises to ``level`` in ``attack`` seconds and fades by ``decay`` dB a second from then on. From
    ``damp`` seconds (0: never) it fades _DAMPING dB a second faster, like a string stopped by a hand.
    """

    at: float  # seconds into the sound when it starts
    freq: float  # Hz; the middle of a band of noise
    level: float  # peak amplitude, where 1.0 is full scale; a band of noise has the RMS of a sine this loud
    decay: float  # dB per second
    attack: float = 0.002  # seconds
    damp: float = 0.0  # seconds after `at`
    bend: float = 1.0  # starts at freq * bend and slides to freq within a few milliseconds
    width: float = 0.0  # Hz; above 0 it is a band of noise this wide instead of a sine


_DAMPING = 1500  # dB per second
_BEND_TIME = 0.005  # seconds: the time constant of a partial's slide to its pitch
_SILENT = 1e-4  # a partial ends when it has faded to this amplitude (-80 dBFS)

# Elsewhere: Vox's own sounds, made to resemble the macOS ones above. Their pitches, levels, decay rates and
# timing were chosen by measuring the macOS sounds; none of Apple's audio is in them. Each row is
# Partial(start s, Hz, level, decay dB/s, ...), and the levels follow the macOS sounds' levels.
_SYNTH_SOUNDS = {
    "start": [  # like Tink: a 698 Hz tick, damped after 16 ms, with a click
        Partial(0, 698, 0.38, 260, attack=0.0035, damp=0.016),
        Partial(0, 1600, 0.048, 5800, attack=0.001, width=1400),
    ],
    "stop": [  # like Pop: a bubble that bounces, ticks at about 680 and 500 Hz, each fainter
        Partial(0.000, 690, 0.27, 1750),
        Partial(0.000, 500, 0.21, 1250),
        Partial(0.000, 1000, 0.0088, 1150),
        Partial(0.063, 9000, 0.0028, 2150, attack=0.001, width=12000),
        Partial(0.065, 680, 0.22, 1650, attack=0.003),
        Partial(0.088, 497, 0.10, 1400),
        Partial(0.132, 680, 0.048, 1550),
        Partial(0.176, 495, 0.013, 1600),
        Partial(0.198, 680, 0.009, 1650),
    ],
    "error": [  # like Basso: a low honk, harmonics of 216 Hz over those of 84 Hz, cut off at 0.16 s
        Partial(0, 216, 0.022, 40, attack=0.019, damp=0.16),
        Partial(0, 435, 0.10, 500, attack=0.057, damp=0.16),
        Partial(0, 650, 0.105, 450, attack=0.034, damp=0.16),
        Partial(0, 866, 0.09, 490, attack=0.013, damp=0.16),
        Partial(0, 84, 0.002, 5, attack=0.069, damp=0.16),
        Partial(0, 168, 0.024, 300, attack=0.076, damp=0.16),
        Partial(0, 252, 0.062, 380, attack=0.040, damp=0.16),
        Partial(0, 335, 0.039, 370, attack=0.022, damp=0.16),
        Partial(0, 1500, 0.006, 390, attack=0.001, damp=0.16, width=1000),
        Partial(0, 3200, 0.0146, 620, attack=0.001, width=2500),
    ],
    "busy": [  # like Funk: three plucked notes, 159, 319 and 401 Hz, each damped, then a faint echo of them
        Partial(0.000, 159, 0.09, 25, attack=0.014, damp=0.09),
        Partial(0.000, 395, 0.036, 520, attack=0.006),
        Partial(0.000, 634, 0.051, 490, attack=0.014),
        Partial(0.060, 779, 0.059, 910, attack=0.013),
        Partial(0.061, 319, 0.18, 40, attack=0.035, damp=0.11),
        Partial(0.064, 1266, 0.13, 530, attack=0.008),
        Partial(0.065, 983, 0.029, 1200, attack=0.007),
        Partial(0.065, 6000, 0.009, 1550, attack=0.001, width=10000),
        Partial(0.148, 401, 0.12, 87, attack=0.049, damp=0.108),
        Partial(0.156, 979, 0.026, 1200, attack=0.01),
        Partial(0.156, 1604, 0.028, 1000, attack=0.0095),
        Partial(0.156, 6000, 0.0062, 1800, attack=0.0015, width=10000),
        Partial(0.098, 159, 0.0009, 28, attack=0.09),
        Partial(0.196, 319, 0.003, 48, attack=0.071),
        Partial(0.253, 401, 0.0056, 49, attack=0.034),
    ],
    "cancel": [  # like Blow: a soft 391 Hz hum that swells in with a 48 Hz flutter, joined by 494 and 587 Hz
        Partial(0.000, 391, 0.19, 280, attack=0.135),
        Partial(0.000, 391, 0.039, 50, attack=0.335),
        Partial(0.000, 343, 0.013, 220, attack=0.066),
        Partial(0.000, 439, 0.013, 200, attack=0.07),
        Partial(0.069, 587, 0.067, 81, attack=0.099),
        Partial(0.104, 494, 0.08, 72, attack=0.067),
    ],
    "pause": [  # like Bottle: three knocks falling in pitch, 494, 246 and 185 Hz, the last one sliding down
        Partial(0.000, 494, 0.14, 620, attack=0.0055),
        Partial(0.000, 2500, 0.0044, 830, attack=0.001, width=3000),
        Partial(0.060, 12500, 0.015, 1450, attack=0.001, width=5000),
        Partial(0.061, 246, 0.135, 300, attack=0.0074),
        Partial(0.139, 185, 0.26, 210, attack=0.006, bend=1.24),
        Partial(0.139, 7000, 0.0062, 1100, attack=0.0015, width=6000),
        Partial(0.249, 185, 0.0019, 47, attack=0.066),
    ],
    "resume": [  # like Glass: a 391 Hz body that swells in under bell partials struck again every 126 ms, softer each time
        Partial(0.000, 391, 0.14, 67, attack=0.103),
        Partial(0.000, 2347, 0.026, 165, attack=0.003),
        Partial(0.000, 5852, 0.017, 88, attack=0.003),
        Partial(0.000, 10988, 0.029, 194, attack=0.003),
        Partial(0.000, 3000, 0.004, 940, attack=0.001, width=4000),
        Partial(0.031, 3135, 0.031, 120, attack=0.003),
        Partial(0.031, 7810, 0.018, 108, attack=0.003),
        Partial(0.031, 14665, 0.036, 306, attack=0.003),
        Partial(0.031, 3000, 0.0046, 1330, attack=0.001, width=4000),
        Partial(0.126, 2347, 0.023, 165, attack=0.003),
        Partial(0.126, 10988, 0.0069, 194, attack=0.003),
        Partial(0.126, 3000, 0.0044, 1330, attack=0.001, width=4000),
        Partial(0.157, 3135, 0.031, 165, attack=0.003),
        Partial(0.157, 14665, 0.004, 306, attack=0.003),
        Partial(0.157, 3000, 0.0052, 1330, attack=0.001, width=4000),
        Partial(0.251, 2347, 0.013, 165, attack=0.003),
        Partial(0.251, 3000, 0.0016, 1330, attack=0.001, width=4000),
        Partial(0.283, 3135, 0.014, 165, attack=0.003),
        Partial(0.283, 3000, 0.0021, 1330, attack=0.001, width=4000),
        Partial(0.377, 2347, 0.0055, 165, attack=0.003),
        Partial(0.409, 3135, 0.0058, 165, attack=0.003),
        Partial(0.503, 2347, 0.0021, 165, attack=0.003),
        Partial(0.535, 3135, 0.0021, 165, attack=0.003),
        Partial(0.629, 2347, 0.0008, 165, attack=0.003),
        Partial(0.660, 3135, 0.001, 165, attack=0.003),
    ],
}

# sounddevice plays one sound at a time for the whole process (play() stops the one before), so when it
# ends is process-wide too, in time.monotonic() seconds
_sd_playing_until = 0.0


def sound_playing_until() -> float:
    """When the sound sounddevice last started ends (time.monotonic()); in the past once none is playing.

    Restarting PortAudio stops that sound, so the device re-scan waits for it. NSSound on macOS is unaffected.
    """
    return _sd_playing_until


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


def _render(p: Partial, rng: np.random.Generator) -> np.ndarray:
    """One partial, from its start until it has faded to _SILENT."""
    fall = 20 * np.log10(p.level / _SILENT)  # dB
    seconds = p.attack + fall / p.decay
    if p.damp and seconds > p.damp:
        seconds = p.damp + (fall - p.decay * (p.damp - p.attack)) / (p.decay + _DAMPING)
    t = np.arange(int(seconds * _SAMPLE_RATE)) / _SAMPLE_RATE
    fade = p.decay * np.maximum(t - p.attack, 0)  # dB
    if p.damp:
        fade += _DAMPING * np.maximum(t - p.damp, 0)
    envelope = p.level * np.exp(fade * (-np.log(10) / 20))
    rise = int(p.attack * _SAMPLE_RATE)
    envelope[:rise] *= np.sin(np.pi / 2 * t[:rise] / p.attack)
    if p.width:
        spectrum = np.fft.rfft(rng.standard_normal(len(t)))
        spectrum[np.abs(np.fft.rfftfreq(len(t), 1 / _SAMPLE_RATE) - p.freq) > p.width / 2] = 0
        noise = np.fft.irfft(spectrum, len(t))
        return envelope * noise / np.sqrt(2 * np.mean(noise**2))  # the RMS of a sine at the same level
    if p.bend != 1:  # sliding from freq * bend down to freq, the phase gets ahead by the extra pitch's integral
        t = t + (p.bend - 1) * _BEND_TIME * (1 - np.exp(-t / _BEND_TIME))
    return envelope * np.sin(2 * np.pi * p.freq * t)


def _synthesize(partials: list[Partial]) -> np.ndarray:
    """A built-in sound: its partials added up, with a short fade-out so it ends on silence."""
    rng = np.random.default_rng(0)  # the same noise at every start
    rendered = [(round(p.at * _SAMPLE_RATE), _render(p, rng)) for p in partials]
    samples = np.zeros(max(start + len(r) for start, r in rendered))
    for start, r in rendered:
        samples[start : start + len(r)] += r
    fade = int(_SAMPLE_RATE * 0.005)
    samples[-fade:] *= np.linspace(1, 0, fade)
    return samples.astype(np.float32)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    """A 16-bit PCM WAV file as float32 frames for sounddevice, and its sample rate."""
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"{8 * wf.getsampwidth()}-bit audio; Vox Transfer plays 16-bit PCM")
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


def _synth_sounds(custom: dict[str, Path]) -> dict[str, tuple[np.ndarray, int]]:
    sounds = {}
    for name, partials in _SYNTH_SOUNDS.items():
        if name in custom:
            try:
                sounds[name] = _read_wav(custom[name])
                continue
            except (OSError, EOFError, ValueError, wave.Error) as e:
                log.warning("Couldn't load %s (%s); using the built-in %s sound", custom[name], e, name)
        sounds[name] = (_synthesize(partials), _SAMPLE_RATE)
    return sounds


class SoundPlayer:
    """Plays audio feedback sounds while config.sounds_enabled is on.

    The sound table is always built, so turning sounds on in a reloaded config works at once.
    Custom WAV files are read once, at startup.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._driver_failed = False
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
            self._sounds = _synth_sounds(custom)

    def play(self, name: str) -> None:
        """Start playing a named sound; it plays on while the caller carries on."""
        global _sd_playing_until
        if self._driver_failed or not self._config.sounds_enabled:
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
                _sd_playing_until = time.monotonic() + len(samples) / rate
        except Exception as e:
            log.warning("Failed to play sound %r: %s", name, e)

    def disable(self) -> None:
        """Stop using audio after the microphone driver stalls; restarting Vox restores feedback."""
        self._driver_failed = True

    def enable(self) -> None:
        """Re-enable audio feedback after the driver recovers."""
        self._driver_failed = False
