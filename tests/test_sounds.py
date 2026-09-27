"""Tests for audio feedback sounds and macOS alert sound mappings."""

import logging
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vox.config import Config
from vox.sounds import _SYSTEM_SOUNDS, SoundPlayer


def _fake_appkit():
    """An AppKit stand-in whose NSSound.soundNamed_ returns one distinct mock per system sound."""
    ns_sound = MagicMock()
    ns_sound.soundNamed_.side_effect = lambda name: MagicMock(name=name)
    return MagicMock(NSSound=ns_sound)


def test_sound_player_macos_alert_mapping(tmp_path):
    """Each cue maps to its own system sound: Tink starts (mic on), Pop stops (mic off)."""
    with patch("sys.platform", "darwin"), \
         patch("vox.sounds._SOUNDS_DIR", tmp_path), \
         patch.dict("sys.modules", {"AppKit": _fake_appkit()}):
        player = SoundPlayer(Config(sounds_enabled=True))

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
    for sound_arr in player._sounds.values():
        assert isinstance(sound_arr, np.ndarray)
        assert sound_arr.dtype == np.float32
        assert len(sound_arr) > 0


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
    assert np.array_equal(sd.play.call_args.args[0], player._sounds["start"])
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
         patch("vox.sounds._SOUNDS_DIR", tmp_path), \
         patch.dict("sys.modules", {"AppKit": appkit}), \
         patch("vox.sounds.sd") as sd, \
         caplog.at_level(logging.WARNING, logger="vox.sounds"):
        player = SoundPlayer(Config(sounds_enabled=True))
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
