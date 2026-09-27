"""Tests for audio feedback sounds, macOS alert sound mappings, and custom sound files."""

import logging
import time
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vox import sounds
from vox.config import Config
from vox.sounds import (
    _SAMPLE_RATE,
    _SYNTH_SOUNDS,
    _SYSTEM_SOUNDS,
    Partial,
    SoundPlayer,
    _synthesize,
    sound_playing_until,
    sounds_dir,
)


@pytest.fixture(autouse=True)
def _no_sound_playing(monkeypatch):
    """Each test starts with no sound playing, and leaves none behind for the daemon's device re-scan."""
    monkeypatch.setattr(sounds, "_sd_playing_until", 0.0)


def _fake_appkit():
    """An AppKit stand-in whose NSSound.soundNamed_ returns one distinct mock per system sound."""
    ns_sound = MagicMock()
    ns_sound.soundNamed_.side_effect = lambda name: MagicMock(name=name)
    return MagicMock(NSSound=ns_sound)


def _config(tmp_path, **kwargs):
    """A config loaded from tmp_path/config.toml, whose custom sounds would be in tmp_path/sounds."""
    config = Config(**kwargs)
    config._config_path = tmp_path / "config.toml"
    return config


def _write_wav(path, samples, rate=22050, channels=1, width=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(np.asarray(samples, dtype=f"<i{width}").tobytes())


def test_sound_player_macos_alert_mapping(tmp_path):
    """Each cue maps to its own system sound: Tink starts (mic on), Pop stops (mic off)."""
    with patch("sys.platform", "darwin"), patch.dict("sys.modules", {"AppKit": _fake_appkit()}):
        player = SoundPlayer(_config(tmp_path, sounds_enabled=True))

    assert player._is_darwin is True
    mapping = {name: sound._extract_mock_name() for name, sound in player._sounds.items()}
    assert mapping == {
        "start": "Tink", "stop": "Pop", "error": "Basso", "busy": "Funk",
        "cancel": "Blow", "pause": "Bottle", "resume": "Glass",
    }


def test_sound_player_linux_fallback():
    """Verify synthetic sounds are generated on non-Darwin platforms including cancel."""
    with patch("sys.platform", "linux"):
        player = SoundPlayer(Config(sounds_enabled=True))
    assert player._is_darwin is False
    assert set(player._sounds) == set(_SYSTEM_SOUNDS)
    for samples, rate in player._sounds.values():
        assert isinstance(samples, np.ndarray)
        assert samples.dtype == np.float32
        assert len(samples) > 0
        assert rate == _SAMPLE_RATE


def test_sound_player_disabled_is_silent():
    """Disabled sounds play nothing, but the table is still built so a reload can turn them on."""
    with patch("sys.platform", "linux"), patch("vox.sounds.sd") as sd:
        player = SoundPlayer(Config(sounds_enabled=False))
        player.play("start")
    sd.play.assert_not_called()
    assert set(player._sounds) == set(_SYSTEM_SOUNDS)


def test_enabling_sounds_after_start_plays(caplog):
    """Turning [sounds] enabled on in a reloaded config takes effect without a restart."""
    config = Config(sounds_enabled=False)
    with patch("sys.platform", "linux"), patch("vox.sounds.sd") as sd, caplog.at_level(logging.WARNING, logger="vox.sounds"):
        player = SoundPlayer(config)
        config.sounds_enabled = True  # what the config reloader does
        player.play("start")
    sd.play.assert_called_once()
    assert np.array_equal(sd.play.call_args.args[0], player._sounds["start"][0])
    assert sd.play.call_args.kwargs == {"samplerate": _SAMPLE_RATE}
    assert not caplog.records


def test_disabling_sounds_after_start_silences():
    config = Config(sounds_enabled=True)
    with patch("sys.platform", "linux"), patch("vox.sounds.sd") as sd:
        player = SoundPlayer(config)
        config.sounds_enabled = False
        player.play("stop")
    sd.play.assert_not_called()


def test_darwin_play_failure_does_not_fall_back_to_sounddevice(tmp_path, caplog):
    """An NSSound error is logged; the NSSound object is never handed to sounddevice."""
    appkit = _fake_appkit()
    with patch("sys.platform", "darwin"), \
         patch.dict("sys.modules", {"AppKit": appkit}), \
         patch("vox.sounds.sd") as sd, \
         caplog.at_level(logging.WARNING, logger="vox.sounds"):
        player = SoundPlayer(_config(tmp_path, sounds_enabled=True))
        player._sounds["error"].play.side_effect = RuntimeError("audio device gone")
        player.play("error")

    sd.play.assert_not_called()
    assert any("audio device gone" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("name", sorted(_SYSTEM_SOUNDS))
def test_every_cue_plays_on_linux(name):
    with patch("sys.platform", "linux"), patch("vox.sounds.sd") as sd:
        SoundPlayer(Config(sounds_enabled=True)).play(name)
    sd.play.assert_called_once()


def test_unknown_sound_warns(caplog):
    with patch("sys.platform", "linux"), patch("vox.sounds.sd") as sd, caplog.at_level(logging.WARNING, logger="vox.sounds"):
        SoundPlayer(Config(sounds_enabled=True)).play("fanfare")
    sd.play.assert_not_called()
    assert any("Unknown sound" in r.getMessage() for r in caplog.records)


# -- The built-in sounds on Linux ----------------------------------------------------------


@pytest.fixture(scope="module")
def built_in():
    """Each built-in sound, as the Linux player makes it."""
    return {name: _synthesize(partials) for name, partials in _SYNTH_SOUNDS.items()}


def _amplitude(samples, seconds, window=0.004):
    """The largest amplitude within `window` seconds from `seconds` into the sound."""
    start = round(seconds * _SAMPLE_RATE)
    return np.abs(samples[start : start + round(window * _SAMPLE_RATE)]).max()


def test_every_cue_has_a_built_in_sound(built_in):
    assert set(built_in) == set(_SYSTEM_SOUNDS)
    with patch("sys.platform", "linux"):
        player = SoundPlayer(Config(sounds_enabled=True))
    for name, samples in built_in.items():
        assert np.array_equal(player._sounds[name][0], samples)


def test_the_built_in_sounds_are_all_different(built_in):
    names = sorted(built_in)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert len(built_in[a]) != len(built_in[b]) or not np.allclose(built_in[a], built_in[b]), (a, b)


def test_the_built_in_sounds_are_audible_and_never_clip(built_in):
    for name, samples in built_in.items():
        assert 0.1 < np.abs(samples).max() < 0.5, name  # headroom: at least 6 dB below full scale


def test_the_built_in_sounds_start_and_end_on_silence(built_in):
    for name, samples in built_in.items():
        assert abs(samples[0]) < 1e-3, name
        assert abs(samples[-1]) < 1e-4, name
        assert np.abs(samples[:5]).max() < 0.1 * np.abs(samples).max(), name  # it fades in: no step, no click


def test_the_built_in_sounds_are_short(built_in):
    seconds = {name: len(samples) / _SAMPLE_RATE for name, samples in built_in.items()}
    assert seconds["start"] < 0.1  # recording starts as it plays, so it is a quick tick
    assert all(0.03 < s < 1.6 for s in seconds.values()), seconds


def test_the_built_in_sounds_are_the_same_every_time(built_in):
    for name, partials in _SYNTH_SOUNDS.items():
        assert np.array_equal(_synthesize(partials), built_in[name]), name


def test_a_partial_rises_to_its_level_then_fades_at_its_decay_rate():
    samples = _synthesize([Partial(0, 1000, 0.3, 40, attack=0.01)])
    assert _amplitude(samples, 0.0, window=0.001) < 0.05
    assert _amplitude(samples, 0.01) == pytest.approx(0.3, rel=0.01)
    assert _amplitude(samples, 0.51) == pytest.approx(0.3 * 10 ** (-40 * 0.5 / 20), rel=0.01)
    assert len(samples) / _SAMPLE_RATE == pytest.approx(0.01 + 20 * np.log10(0.3 / 1e-4) / 40, abs=0.001)


def test_a_damped_partial_dies_quickly_from_then_on():
    ringing = _synthesize([Partial(0, 1000, 0.3, 40)])
    damped = _synthesize([Partial(0, 1000, 0.3, 40, damp=0.1)])
    assert _amplitude(damped, 0.05) == pytest.approx(_amplitude(ringing, 0.05))
    assert _amplitude(damped, 0.12) < _amplitude(ringing, 0.12) / 10
    assert len(damped) < 0.2 * _SAMPLE_RATE < len(ringing)


def test_a_band_of_noise_stays_in_its_band():
    samples = _synthesize([Partial(0, 6000, 0.1, 20, width=2000)])
    power = np.abs(np.fft.rfft(samples)) ** 2
    freqs = np.fft.rfftfreq(len(samples), 1 / _SAMPLE_RATE)
    assert power[(freqs > 5000) & (freqs < 7000)].sum() > 0.99 * power.sum()


# -- Custom sounds -------------------------------------------------------------------------


def test_custom_sounds_live_next_to_config_toml(tmp_path):
    assert sounds_dir(_config(tmp_path)) == tmp_path / "sounds"
    assert sounds_dir(Config()).name == "sounds"  # ~/.config/vox/sounds when no file was loaded


def test_a_custom_wav_replaces_that_cue_on_linux(tmp_path):
    _write_wav(tmp_path / "sounds" / "start.wav", [[0, 16384], [-16384, 32767]], rate=22050, channels=2)
    with patch("sys.platform", "linux"), patch("vox.sounds.sd") as sd:
        player = SoundPlayer(_config(tmp_path, sounds_enabled=True))
        player.play("start")
        player.play("stop")

    start, stop = sd.play.call_args_list
    assert start.kwargs == {"samplerate": 22050}
    assert np.allclose(start.args[0], [[0, 0.5], [-0.5, 32767 / 32768]])
    assert start.args[0].dtype == np.float32
    assert stop.kwargs == {"samplerate": _SAMPLE_RATE}  # no stop.wav: the built-in tone


@pytest.mark.parametrize("content", ["not a wav", "24-bit"])
def test_an_unreadable_custom_wav_falls_back_to_the_built_in_sound(tmp_path, caplog, content):
    path = tmp_path / "sounds" / "error.wav"
    if content == "24-bit":
        path.parent.mkdir()
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(3)
            wf.setframerate(44100)
            wf.writeframes(b"\x00\x00\x01" * 10)
    else:
        path.parent.mkdir()
        path.write_text(content)
    with patch("sys.platform", "linux"), patch("vox.sounds.sd") as sd, caplog.at_level(logging.WARNING, logger="vox.sounds"):
        player = SoundPlayer(_config(tmp_path, sounds_enabled=True))
        player.play("error")
    assert sd.play.call_args.kwargs == {"samplerate": _SAMPLE_RATE}
    assert any(str(path) in r.getMessage() for r in caplog.records)


def test_a_custom_wav_replaces_that_cue_on_macos(tmp_path):
    custom = tmp_path / "sounds" / "stop.wav"
    _write_wav(custom, [0, 1, 2])
    appkit = _fake_appkit()
    loaded = MagicMock(name="custom stop")
    appkit.NSSound.alloc.return_value.initWithContentsOfFile_byReference_.return_value = loaded
    with patch("sys.platform", "darwin"), patch.dict("sys.modules", {"AppKit": appkit}):
        player = SoundPlayer(_config(tmp_path, sounds_enabled=True))

    appkit.NSSound.alloc.return_value.initWithContentsOfFile_byReference_.assert_called_once_with(str(custom), True)
    assert player._sounds["stop"] is loaded
    assert player._sounds["start"]._extract_mock_name() == "Tink"


def test_a_custom_file_macos_cannot_load_falls_back_to_the_system_sound(tmp_path, caplog):
    (tmp_path / "sounds").mkdir()
    (tmp_path / "sounds" / "start.wav").write_text("not audio")
    appkit = _fake_appkit()
    appkit.NSSound.alloc.return_value.initWithContentsOfFile_byReference_.return_value = None
    with patch("sys.platform", "darwin"), patch.dict("sys.modules", {"AppKit": appkit}), \
         caplog.at_level(logging.WARNING, logger="vox.sounds"):
        player = SoundPlayer(_config(tmp_path, sounds_enabled=True))
    assert player._sounds["start"]._extract_mock_name() == "Tink"
    assert any("start.wav" in r.getMessage() for r in caplog.records)


# -- When the sound ends (the device re-scan waits for it) -----------------------------------


def test_a_linux_sound_notes_when_it_ends(tmp_path):
    _write_wav(tmp_path / "sounds" / "cancel.wav", np.zeros(2 * 22050), rate=22050)  # 2 s
    with patch("sys.platform", "linux"), patch("vox.sounds.sd"):
        player = SoundPlayer(_config(tmp_path, sounds_enabled=True))
        before = time.monotonic()
        player.play("cancel")
        assert before + 2 <= sound_playing_until() <= time.monotonic() + 2

        start = len(player._sounds["start"][0]) / _SAMPLE_RATE
        before = time.monotonic()
        player.play("start")  # sounddevice stops the long sound for this short one
        assert before + start <= sound_playing_until() <= time.monotonic() + start


def test_a_sound_that_does_not_play_through_sounddevice_notes_nothing(tmp_path):
    with patch("sys.platform", "linux"), patch("vox.sounds.sd"):
        SoundPlayer(_config(tmp_path, sounds_enabled=False)).play("error")
    with patch("sys.platform", "darwin"), patch.dict("sys.modules", {"AppKit": _fake_appkit()}):
        SoundPlayer(_config(tmp_path, sounds_enabled=True)).play("error")  # NSSound, which a PortAudio restart leaves alone
    assert sound_playing_until() == 0.0
