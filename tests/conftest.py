import os
import sys

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class MemoryKeyring(KeyringBackend):
    """An in-memory keyring, so no test can reach the real Keychain or Secret Service."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.items.pop((service, username), None) is None:
            raise PasswordDeleteError("No such password")


@pytest.fixture(autouse=True)
def _isolate_history_db(tmp_path, monkeypatch):
    """Keep tests from touching the real ~/.local/share/vox/history.db."""
    monkeypatch.setattr("vox.history.DEFAULT_HISTORY_PATH", tmp_path / "history.db")


@pytest.fixture(autouse=True)
def memory_keyring(tmp_path, monkeypatch) -> MemoryKeyring:
    """Keep tests from touching the real keychain, the real key files, or an OPENAI_API_KEY in the shell."""
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("vox.keystore.FALLBACK_PATH", tmp_path / "vox.env")
    monkeypatch.setattr("vox.keystore.DEFAULT_CONFIG_PATH", tmp_path / "default-config.toml")  # migration's default
    return backend


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
