"""Tests for audio feedback sounds and macOS alert sound mappings."""

import sys
from unittest.mock import MagicMock, patch

from vox.config import Config
from vox.sounds import SoundPlayer


def test_sound_player_macos_alert_mapping():
    """Verify that macOS alert sounds map Tink to start (mic on) and Pop to stop (mic off)."""
    config = Config(sounds_enabled=True)

    with patch("sys.platform", "darwin"):
        mock_ns_sound = MagicMock()
        mock_sound_named = MagicMock()
        mock_ns_sound.soundNamed_ = mock_sound_named

        with patch.dict("sys.modules", {"AppKit": MagicMock(NSSound=mock_ns_sound)}):
            player = SoundPlayer(config)
            assert player._is_darwin is True

            # Verify NSSound.soundNamed_ was called for start with "Tink" and stop with "Pop"
            mock_sound_named.assert_any_call("Tink")
            mock_sound_named.assert_any_call("Pop")
            mock_sound_named.assert_any_call("Basso")
            mock_sound_named.assert_any_call("Funk")
            mock_sound_named.assert_any_call("Blow")


def test_sound_player_linux_fallback():
    """Verify synthetic sounds are generated on non-Darwin platforms including cancel."""
    import numpy as np

    config = Config(sounds_enabled=True)
    with patch("sys.platform", "linux"):
        player = SoundPlayer(config)
        assert player._is_darwin is False
        assert set(player._sounds.keys()) == {"start", "stop", "error", "busy", "cancel"}
        for name, sound_arr in player._sounds.items():
            assert isinstance(sound_arr, np.ndarray)
            assert sound_arr.dtype == np.float32
            assert len(sound_arr) > 0


def test_sound_player_disabled():
    """Verify sounds dict is empty when sounds are disabled."""
    config = Config(sounds_enabled=False)
    player = SoundPlayer(config)
    assert player._sounds == {}
