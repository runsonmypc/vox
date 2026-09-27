"""Tests for audio feedback sounds, macOS alert sound mappings, and custom sound files."""

import logging
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vox.config import Config
from vox.sounds import _SAMPLE_RATE, _SYSTEM_SOUNDS, SoundPlayer, sounds_dir


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
