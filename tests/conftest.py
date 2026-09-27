import atexit
import ipaddress
import os
import shutil
import socket
import sys
import tempfile

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

# Modules compute paths such as ~/.config/vox/config.toml when first imported, so point HOME at a
# scratch directory before any test imports vox: nothing a test does can reach the user's real files
_HOME = tempfile.mkdtemp(prefix="vox-test-home-")
os.environ["HOME"] = _HOME
atexit.register(shutil.rmtree, _HOME, ignore_errors=True)

# pystray's Linux backends load GTK 3, after which GTK 4 can no longer load in the same process.
# The tray tests use fake icons there, so the dummy backend is enough. macOS keeps its real backend.
if sys.platform.startswith("linux"):
    os.environ.setdefault("PYSTRAY_BACKEND", "dummy")


def pytest_configure(config):
    config.addinivalue_line("markers", "gtk: builds GTK 4 windows; CI runs these apart from the pystray tests")


def pytest_collection_modifyitems(items):
    for item in items:
        if "gtk" in getattr(item, "fixturenames", ()):
            item.add_marker("gtk")


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
def _isolate_config(tmp_path, monkeypatch):
    """Give each test its own default config.toml, in every module that imported the name."""
    path = tmp_path / "default-config.toml"  # the same file the keystore migration fixture uses
    monkeypatch.setattr("vox.config.DEFAULT_CONFIG_PATH", path)
    for name in ("vox.daemon", "vox.ui.tray", "vox.ui.vocab_window", "vox.ui.hotkey_window"):
        module = sys.modules.get(name)  # importing vox.daemon here would need an X display on Linux
        if module is not None:
            monkeypatch.setattr(module, "DEFAULT_CONFIG_PATH", path)


@pytest.fixture(autouse=True)
def memory_keyring(tmp_path, monkeypatch) -> MemoryKeyring:
    """Keep tests from touching the real keychain, the real key files, or an OPENAI_API_KEY in the shell."""
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("vox.keystore.FALLBACK_PATH", tmp_path / "vox.env")
    monkeypatch.setattr("vox.keystore.DEFAULT_CONFIG_PATH", tmp_path / "default-config.toml")  # migration's default
    return backend


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    if host is None or host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_network(monkeypatch) -> list:
    """Fail any test that tries to reach another machine (such as OpenAI); loopback servers still work.

    Returns the blocked (host, port) attempts, which a test that expects one must clear.
    """
    attempts: list = []
    real_getaddrinfo = socket.getaddrinfo
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def blocked(host, port):
        attempts.append((host, port))
        return OSError(f"tests must not use the network: {host}:{port}")

    def getaddrinfo(host, port, *args, **kwargs):
        if not _is_local(host):
            raise socket.gaierror(str(blocked(host, port)))
        return real_getaddrinfo(host, port, *args, **kwargs)

    def check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(address[0]):
            raise blocked(address[0], address[1])

    def connect(sock, address):
        check(sock, address)
        return real_connect(sock, address)

    def connect_ex(sock, address):
        check(sock, address)
        return real_connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    yield attempts
    if attempts:
        pytest.fail(f"Test tried to reach the network: {attempts}")


def _gtk_unavailable(reason: str):
    """Skip, unless CI set VOX_REQUIRE_GTK=1 to prove the GTK 4 windows really were built."""
    if os.environ.get("VOX_REQUIRE_GTK") == "1":
        pytest.fail(f"VOX_REQUIRE_GTK=1 but {reason}")
    pytest.skip(reason)


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
        _gtk_unavailable("No display")
    try:
        from vox.ui.gtk import common
    except (ImportError, ValueError) as e:
        if "already loaded" in str(e):
            pytest.fail(f"Something in this test process loaded another GTK first: {e}")
        _gtk_unavailable(f"GTK 4 and libadwaita unavailable: {e}")
    common.setup()
    return common
