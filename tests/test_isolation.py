"""The guards in conftest.py that keep tests away from the user's files and the network."""

import os
import pwd
import socket
import sys
from pathlib import Path

import pytest

import vox.config
from vox.config import load_config, update_transcription_mode


def test_home_is_a_scratch_directory():
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert Path.home() != real_home
    assert real_home not in vox.config.DEFAULT_CONFIG_PATH.parents


def test_default_config_path_is_per_test(tmp_path):
    assert vox.config.DEFAULT_CONFIG_PATH.parent == tmp_path
    for name in ("vox.daemon", "vox.ui.tray", "vox.ui.vocab_window"):
        if name in sys.modules:
            assert sys.modules[name].DEFAULT_CONFIG_PATH == vox.config.DEFAULT_CONFIG_PATH


def test_mode_switch_without_a_config_path_stays_in_the_test(tmp_path):
    # What the daemon and tray do for a Config() that was not loaded from a file
    update_transcription_mode(vox.config.DEFAULT_CONFIG_PATH, "streaming")
    assert load_config(tmp_path / "default-config.toml").mode == "streaming"


def test_pystray_uses_the_dummy_backend():
    pytest.importorskip("pystray")
    assert "pystray._dummy" in sys.modules
    # The Linux backends would load GTK 3 and break the GTK 4 window tests
    assert not {"pystray._appindicator", "pystray._gtk", "pystray._xorg"} & sys.modules.keys()


def test_remote_hosts_are_blocked(no_network):
    with pytest.raises(OSError):
        socket.create_connection(("api.openai.com", 443), timeout=1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(OSError):
        sock.connect(("192.0.2.1", 443))
    assert no_network == [("api.openai.com", 443), ("192.0.2.1", 443)]
    no_network.clear()  # expected here; any other test that tries fails at teardown


def test_loopback_still_works(no_network):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=1):
            conn, _ = server.accept()
            conn.close()
    assert no_network == []


def test_gtk_tests_are_marked(request):
    for item in request.session.items:
        uses_gtk = "gtk" in getattr(item, "fixturenames", ())
        assert bool(item.get_closest_marker("gtk")) == uses_gtk, item.nodeid


def test_required_gtk_fails_instead_of_skipping(monkeypatch):
    from conftest import _gtk_unavailable

    monkeypatch.setenv("VOX_REQUIRE_GTK", "1")
    with pytest.raises(pytest.fail.Exception, match="VOX_REQUIRE_GTK=1 but No display"):
        _gtk_unavailable("No display")
    monkeypatch.delenv("VOX_REQUIRE_GTK")
    with pytest.raises(pytest.skip.Exception):
        _gtk_unavailable("No display")
