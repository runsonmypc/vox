import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _isolate_history_db(tmp_path, monkeypatch):
    """Keep tests from touching the real ~/.local/share/vox/history.db."""
    monkeypatch.setattr("vox.history.DEFAULT_HISTORY_PATH", tmp_path / "history.db")


@pytest.fixture
def appkit():
    """AppKit, for building (never showing) the macOS windows. Needs a logged-in GUI session."""
    if sys.platform != "darwin":
        pytest.skip("AppKit windows are macOS-only")
    AppKit = pytest.importorskip("AppKit")
    from Quartz import CGSessionCopyCurrentDictionary

    if CGSessionCopyCurrentDictionary() is None:
        pytest.skip("No GUI session")
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    return AppKit


@pytest.fixture
def gtk():
    """The GTK 4 and libadwaita setup module, for building the Linux windows. Needs a display (e.g. xvfb-run)."""
    if sys.platform == "darwin":
        pytest.skip("GTK windows are Linux-only")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("No display")
    try:
        from vox.ui.gtk import common
    except (ImportError, ValueError) as e:
        pytest.skip(f"GTK 4 and libadwaita unavailable: {e}")
    common.setup()
    return common
