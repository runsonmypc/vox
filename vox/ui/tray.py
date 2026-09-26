"""Tray icon (macOS menu bar, Linux AppIndicator): status, input device picker, pause toggle, recent dictations.

Cocoa and GTK both want the tray on the main thread, so the tray owns the main
thread while the asyncio daemon runs in its own thread. Menu callbacks run on
the main thread and reach the daemon via ``loop.call_soon_threadsafe``; daemon
updates reach the tray via ``dispatch`` (``AppHelper.callAfter`` on macOS,
``GLib.idle_add`` on Linux). Tray state is only read and written on the main
thread.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from typing import Any, Callable

import sounddevice as sd

from ..config import DEFAULT_CONFIG_PATH, Config
from ..errors import ConfigError
from ..history import HistoryDB, HistoryRecord
from ..whisper_cpp import WhisperCppTranscriber
from .icons import IconState, is_template, make_icon

log = logging.getLogger(__name__)

_STATUS_TEXT = {
    IconState.IDLE: "Idle",
    IconState.RECORDING: "Recording…",
    IconState.PROCESSING: "Processing…",
    IconState.PAUSED: "Paused",
}
_RECENT_COUNT = 3
_RECENT_LABEL_CHARS = 48

RECENT_HEADER = "Recent Dictations — click to copy"

HISTORY_WINDOW = "vox.ui.history_window"
VOCAB_WINDOW = "vox.ui.vocab_window"


def create_tray(config: Config) -> TrayManager | None:
    """Build the tray icon, or return None when the daemon should run headless."""
    try:
        return _create_macos_tray(config) if sys.platform == "darwin" else _create_linux_tray(config)
    except Exception as e:
        log.warning("Tray icon unavailable (%s); running headless", e)
        return None


def _create_macos_tray(config: Config) -> TrayManager | None:
    if not _has_gui_session():
        log.info("No GUI session; running headless without a menu bar icon")
        return None
    import AppKit
    from PyObjCTools import AppHelper

    # Menu bar only: no Dock icon or app switcher entry
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    return TrayManager(config, icon_factory=_darwin_icon_class(), dispatch=AppHelper.callAfter)


def _create_linux_tray(config: Config) -> TrayManager | None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        log.info("No graphical session; running headless without a tray icon")
        return None
    import pystray

    if not pystray.Icon.HAS_MENU:
        # pystray fell back to its bare X11 backend, which has no menu
        log.warning("Tray icon needs python3-gi and AppIndicator (run install.sh); running headless")
        return None
    if pystray.Icon.__module__.endswith("_appindicator") and not _has_tray_host():
        log.info(
            "No system tray host is running (on GNOME, enable the AppIndicator extension); "
            "the icon appears once one starts"
        )
    return TrayManager(config, icon_factory=_linux_icon_class(pystray), dispatch=_glib_dispatch, light=True)


def _has_gui_session() -> bool:
    try:
        from Quartz import CGSessionCopyCurrentDictionary
    except ImportError:
        return False
    return CGSessionCopyCurrentDictionary() is not None


def _has_tray_host() -> bool:
    """Whether a StatusNotifierItem host (the Linux tray) is on the session bus."""
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)), GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE, 1000, None,
        )
        return reply.unpack()[0]
    except Exception:
        return True  # unknown: skip the notice


def _glib_dispatch(fn: Callable[..., Any], *args: Any) -> None:
    """Run ``fn`` on the GTK main loop, like ``AppHelper.callAfter`` on macOS."""
    from gi.repository import GLib

    def call() -> bool:
        try:
            fn(*args)
        except Exception:
            log.exception("Tray update failed")
        return GLib.SOURCE_REMOVE

    GLib.idle_add(call)


def _linux_icon_class(pystray: Any) -> type:
    class VoxIcon(pystray.Icon):
        """pystray's Linux icon, tolerating a missing notification server at exit."""

        def _finalize(self) -> None:
            # pystray always closes its notification on exit, which raises without org.freedesktop.Notifications
            try:
                super()._finalize()
            except Exception as e:
                log.debug("Tray cleanup: %s", e)

    return VoxIcon


def _darwin_icon_class() -> type:
    import AppKit
    import pystray

    class VoxIcon(pystray.Icon):
        """pystray's macOS icon, drawn at Retina resolution and as a template image when monochrome."""

        template = True

        def _assert_image(self) -> None:
            try:
                png = _png_bytes(self._icon)
                image = AppKit.NSImage.alloc().initWithData_(AppKit.NSData.dataWithBytes_length_(png, len(png)))
                side = self._status_bar.thickness()
                image.setSize_((side, side))
                image.setTemplate_(self.template)
                self._icon_image = image
                self._status_item.button().setImage_(image)
            except Exception:
                log.debug("Custom status icon rendering failed; using pystray's", exc_info=True)
                super()._assert_image()

    return VoxIcon


def _png_bytes(image: Any) -> bytes:
    import io

    buf = io.BytesIO()
    image.save(buf, "png")
    return buf.getvalue()


def _launch_window(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(args, stdin=subprocess.DEVNULL)


def _focus_window(proc: subprocess.Popen, reopen: list[str] | None) -> None:
    """Bring a window process to the front: just launched, or already open when ``reopen`` is its command."""
    try:
        if sys.platform == "darwin":
            _hand_focus_to(proc)
        elif reopen is not None:
            # The windows are single-instance GTK apps: a second launch presents the open window and exits
            _launch_window(reopen)
    except Exception as e:
        log.debug("Could not bring the window forward: %s", e)


def _hand_focus_to(proc: subprocess.Popen, wait: float = 15.0) -> None:
    """Activate a window process on macOS once it has started.

    Since macOS 14 an app can't activate itself unless the user just
    interacted with it, so a freshly spawned window would open behind the
    frontmost app. The menu click is interaction with Vox, so Vox takes focus
    here and passes it on to the window process.
    """
    import time

    import AppKit
    from PyObjCTools import AppHelper

    app = AppKit.NSApplication.sharedApplication()
    if app.respondsToSelector_("activate"):
        app.activate()
    else:
        app.activateIgnoringOtherApps_(True)
    deadline = time.monotonic() + wait

    def attempt() -> None:
        target = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(proc.pid)
        if target is not None and target.isFinishedLaunching():
            target.activateWithOptions_(AppKit.NSApplicationActivateAllWindows)
        elif proc.poll() is None and time.monotonic() < deadline:
            AppHelper.callLater(0.05, attempt)

    attempt()


def copy_to_clipboard(text: str) -> None:
    from ..injector import set_clipboard

    set_clipboard(text)


class TrayManager:
    def __init__(
        self,
        config: Config,
        icon_factory: Callable[..., Any],
        dispatch: Callable[..., None],
        launcher: Callable[[list[str]], subprocess.Popen] = _launch_window,
        copy: Callable[[str], None] = copy_to_clipboard,
        light: bool = False,
        focus: Callable[[subprocess.Popen, list[str] | None], None] | None = None,
    ) -> None:
        import pystray

        self._pystray = pystray
        self._config = config
        self._dispatch = dispatch
        self._launcher = launcher
        self._focus = focus or _focus_window
        self._copy = copy

        # Main thread only
        self._state = IconState.IDLE
        self._paused = False
        self._recent: list[HistoryRecord] = []
        self._windows: dict[str, subprocess.Popen] = {}
        self._images = {state: make_icon(state, light=light) for state in IconState}

        # Set once by attach() from the daemon thread
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[str] | None = None
        self._history: HistoryDB | None = None
        self._main_task: asyncio.Task | None = None

        self._icon = icon_factory(
            "vox",
            self._images[IconState.IDLE],
            self._title(IconState.IDLE),
            pystray.Menu(self._menu_items),
        )

    # -- Main thread -------------------------------------------------------

    def run(self) -> None:
        """Run the tray event loop. Blocks until stop()."""
        try:
            self._icon.run(setup=lambda icon: self._dispatch(setattr, icon, "visible", True))
        finally:
            self._close_windows()

    # -- Any thread --------------------------------------------------------

    def attach(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[str],
        history: HistoryDB | None,
        main_task: asyncio.Task,
    ) -> None:
        """Connect the tray to the running daemon."""
        self._loop = loop
        self._queue = queue
        self._history = history
        self._main_task = main_task
        self._dispatch(self._refresh_recent)

    def set_state(self, state: str) -> None:
        """Show a daemon state: IDLE, RECORDING, or PROCESSING."""
        self._dispatch(self._apply_state, IconState(state))

    def set_paused(self, paused: bool) -> None:
        self._dispatch(self._apply_paused, paused)

    def history_changed(self) -> None:
        self._dispatch(self._refresh_recent)

    def mode_changed(self) -> None:
        self._dispatch(self._icon.update_menu)

    def stop(self) -> None:
        self._dispatch(self._icon.stop)

    def request_quit(self) -> None:
        """Ask the daemon to shut down; it stops the tray on its way out."""
        if self._main_task is None:
            self._dispatch(self._icon.stop)
        else:
            self._call_daemon(self._main_task.cancel)

    # -- Rendering (main thread) ------------------------------------------

    @property
    def _shown(self) -> IconState:
        return IconState.PAUSED if self._paused and self._state is IconState.IDLE else self._state

    @staticmethod
    def _title(state: IconState) -> str:
        return f"Vox — {_STATUS_TEXT[state]}"

    def _render(self) -> None:
        shown = self._shown
        self._icon.template = is_template(shown)
        self._icon.icon = self._images[shown]
        self._icon.title = self._title(shown)
        self._icon.update_menu()

    def _apply_state(self, state: IconState) -> None:
        self._state = state
        self._render()

    def _apply_paused(self, paused: bool) -> None:
        self._paused = paused
        self._render()

    def _refresh_recent(self) -> None:
        if self._history is None:
            return
        try:
            self._recent = self._history.recent(_RECENT_COUNT)
        except Exception as e:
            log.debug("Could not read recent dictations: %s", e)
            return
        self._icon.update_menu()

    # -- Menu (main thread) -----------------------------------------------

    def _menu_items(self):
        # Sections: status, dictation controls, recent transcripts, windows, quit
        Item, Menu = self._pystray.MenuItem, self._pystray.Menu
        yield Item(self._title(self._shown), None, enabled=False)
        yield Menu.SEPARATOR
        yield Item("Pause Dictation", self._toggle_pause, checked=lambda _: self._paused)
        yield Item("Input Device", Menu(self._device_items))
        yield Item("Transcription", Menu(self._transcription_items))
        yield Menu.SEPARATOR
        yield Item(RECENT_HEADER if self._recent else "No dictations yet", None, enabled=False)
        for rec in self._recent:
            yield Item(_recent_label(rec.text), self._copier(rec.text))
        yield Menu.SEPARATOR
        yield Item("Search History…", self._open_history, enabled=self._history is not None)
        yield Item("Vocabulary & Snippets…", self._open_vocab)
        yield Menu.SEPARATOR
        yield Item("Quit Vox", self._quit)

    def _device_items(self):
        Item = self._pystray.MenuItem
        devices = self._input_devices()
        selected = _selected_device(self._config.audio_device, devices)
        yield Item("System Default", self._device_setter(None), checked=_is(selected, None), radio=True)
        for index, name in devices:
            yield Item(name, self._device_setter(index), checked=_is(selected, index), radio=True)

    def _transcription_items(self):
        Item = self._pystray.MenuItem
        for label, mode in (
            ("OpenAI (batch)", "batch"),
            ("OpenAI (streaming)", "streaming"),
            ("Local (whisper.cpp)", "whisper_cpp"),
        ):
            yield Item(
                label, self._mode_setter(mode),
                checked=lambda _, mode=mode: self._config.mode == mode,
                enabled=lambda _, mode=mode: self._can_select_mode(mode),
                radio=True,
            )

    def _can_select_mode(self, mode: str) -> bool:
        if self._state is not IconState.IDLE:
            return False
        if mode == self._config.mode:
            return True
        if mode != "whisper_cpp":
            return bool(self._config.openai_api_key)
        try:
            WhisperCppTranscriber(self._config)
        except ConfigError:
            return False
        return True

    def _mode_setter(self, mode: str):
        def action(icon, item):
            self._send(f"mode:{mode}")

        return action

    def _input_devices(self) -> list[tuple[int, str]]:
        try:
            devices = sd.query_devices()
        except Exception as e:
            log.warning("Failed to query audio devices: %s", e)
            return []
        return [
            (i, d.get("name", f"Device {i}"))
            for i, d in enumerate(devices)
            if d.get("max_input_channels", 0) >= self._config.channels
        ]

    def _device_setter(self, spec: int | None):
        def action(icon, item):
            def apply() -> None:
                self._config.audio_device = spec
                log.info("Input device set to %s from menu bar", item.text)
                self._dispatch(self._icon.update_menu)

            self._call_daemon(apply)

        return action

    def _copier(self, text: str):
        def action(icon, item):
            try:
                self._copy(text)
                log.info("Copied recent dictation to clipboard (%d chars)", len(text))
            except Exception as e:
                log.warning("Failed to copy dictation: %s", e)

        return action

    def _toggle_pause(self, icon, item) -> None:
        self._send("resume" if self._paused else "pause")

    def _open_history(self, icon, item) -> None:
        if self._history is not None:
            self._open_window(HISTORY_WINDOW, "--db", str(self._history.path))

    def _open_vocab(self, icon, item) -> None:
        path = self._config.config_path or DEFAULT_CONFIG_PATH
        self._open_window(VOCAB_WINDOW, "--config", str(path))

    def _quit(self, icon, item) -> None:
        log.info("Quit requested from menu bar")
        self.request_quit()

    # -- Plumbing ----------------------------------------------------------

    def _open_window(self, module: str, *args: str) -> None:
        command = [sys.executable, "-m", module, *args]
        proc = self._windows.get(module)
        if proc is not None and proc.poll() is None:
            log.info("%s is already open; bringing it forward", module)
            self._focus(proc, command)
            return
        try:
            proc = self._windows[module] = self._launcher(command)
        except Exception as e:
            log.warning("Failed to open %s: %s", module, e)
            return
        self._focus(proc, None)

    def _close_windows(self) -> None:
        for proc in self._windows.values():
            if proc.poll() is None:
                proc.terminate()
        self._windows.clear()

    def _send(self, event: str) -> None:
        if self._queue is not None:
            self._call_daemon(self._queue.put_nowait, event)

    def _call_daemon(self, fn: Callable[..., Any], *args: Any) -> None:
        if self._loop is None:
            log.debug("Daemon not attached yet; ignoring menu action")
            return
        try:
            self._loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            log.debug("Daemon loop is closed; ignoring menu action")


def _recent_label(text: str) -> str:
    """Quoted one-line preview, so transcripts read as content rather than commands."""
    flat = " ".join(text.split())
    if len(flat) > _RECENT_LABEL_CHARS:
        flat = flat[: _RECENT_LABEL_CHARS - 1].rstrip() + "…"
    return f"“{flat}”"


def _selected_device(spec: int | str | None, devices: list[tuple[int, str]]) -> int | None:
    """Index of the configured input device among ``devices``, or None for the system default."""
    if isinstance(spec, int):
        return spec if any(i == spec for i, _ in devices) else None
    if isinstance(spec, str):
        needle = spec.lower().strip()
        return next((i for i, name in devices if needle in name.lower()), None)
    return None


def _is(selected: int | None, index: int | None) -> Callable[[Any], bool]:
    return lambda _item: selected == index
