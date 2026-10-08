"""Tray icon (macOS menu bar, Linux AppIndicator): status, pausing, recent dictations, and the History and Settings windows.

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
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

from ..config import DEFAULT_CONFIG_PATH, Config
from ..history import HistoryDB, HistoryRecord
from ..modes import LABELS, mode_problem
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
_PROBLEM_CHARS = 72
_MENU_RETRY_SECONDS = 0.05

RECENT_HEADER = "Click a recent dictation to copy it"
CONFIG_ERROR = "Settings file has an error"
SETTINGS_OPEN = "Dictation is off while Settings is open"

HISTORY_WINDOW = "vox.ui.history_window"
SETTINGS_WINDOW = "vox.ui.settings_window"
SET_KEY = "Set API Key…"
SETTINGS = "Settings…"


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
        log.debug("Could not look for a tray host", exc_info=True)
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


def _menu_is_tracking() -> bool:
    """Whether a menu is open: AppKit runs the main run loop in event-tracking mode while it tracks one."""
    import AppKit

    return AppKit.NSRunLoop.currentRunLoop().currentMode() == AppKit.NSEventTrackingRunLoopMode


def _darwin_icon_class() -> type:
    import AppKit
    import pystray
    from PyObjCTools import AppHelper

    class VoxIcon(pystray.Icon):
        """pystray's macOS icon, drawn at Retina resolution and as a template image when monochrome."""

        template = True
        _menu_update_pending = False

        def _update_menu(self) -> None:
            # pystray resolves a click by the item's tag in the newest callbacks list, so rebuilding the menu while
            # it is open makes a click on the menu still on screen run another item. callLater fires only in the
            # default run-loop mode: after the menu closes and the clicked item's action has run.
            if _menu_is_tracking():
                if not self._menu_update_pending:
                    self._menu_update_pending = True
                    AppHelper.callLater(_MENU_RETRY_SECONDS, self._deferred_update_menu)
                return
            super()._update_menu()

        def _deferred_update_menu(self) -> None:
            self._menu_update_pending = False
            self._update_menu()

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
            _reap(_launch_window(reopen))
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
        self._notice: str | None = None
        self._settings_open = False  # dictation is off meanwhile
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
            self._status_line(),
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
        self._dispatch(self._render)  # the status line depends on whether the mode needs a key

    def key_changed(self) -> None:
        """The daemon re-read the API key."""
        self._dispatch(self._render)

    def set_notice(self, text: str | None) -> None:
        """Show a persistent problem (e.g. Wayland, a silent microphone) in the status line; None clears it."""
        self._dispatch(self._apply_notice, text)

    def open_settings(self, page: str | None = None) -> None:
        """Open Settings on ``page`` (such as "transcription"), or bring it forward if it is open."""
        self._dispatch(self._open_settings, page)

    def stop(self) -> None:
        self._dispatch(self._icon.stop)

    def dispatch_ui(self, callback: Callable[[], None]) -> None:
        """Share the existing GUI loop with optional native feedback."""
        self._dispatch(callback)

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
        return f"Vox Transfer · {_STATUS_TEXT[state]}"

    def _key_problem(self) -> str | None:
        """Why dictation can't reach OpenAI, if the mode needs it and there is no key."""
        if not self._config.uses_openai or self._config.openai_api_key:
            return None
        return "Can’t read the keyring" if self._config.api_key_error else "API key needed"

    def _problem(self) -> str | None:
        """What the status line reports while idle, most urgent first: config.toml, the key, the mode,
        Settings being open, then a notice.

        A config.toml that didn't load comes first: until it does, the mode (and so whether a key is
        needed at all) is only the default's.
        """
        config = self._config
        problem = (
            (CONFIG_ERROR if config.config_error else None) or self._key_problem() or config.mode_error
            or (SETTINGS_OPEN if self._settings_open else None) or self._notice
        )
        return _one_line(problem, _PROBLEM_CHARS) if problem else None

    def _status_line(self) -> str:
        shown = self._shown
        problem = self._problem() if shown in (IconState.IDLE, IconState.PAUSED) else None
        return f"Vox Transfer · {problem}" if problem else self._title(shown)

    def _render(self) -> None:
        shown = self._shown
        self._icon.template = is_template(shown)
        self._icon.icon = self._images[shown]
        self._icon.title = self._status_line()
        self._icon.update_menu()

    def _apply_state(self, state: IconState) -> None:
        self._state = state
        self._render()

    def _apply_notice(self, text: str | None) -> None:
        self._notice = text
        self._render()

    def _apply_settings_open(self, open_: bool) -> None:
        self._settings_open = open_
        self._render()

    def _apply_paused(self, paused: bool) -> None:
        self._paused = paused
        self._render()

    def _refresh_recent(self) -> None:
        if self._history is None:
            return
        try:
            self._recent = [rec for rec in self._history.search(limit=200) if rec.text.strip()][:_RECENT_COUNT]
        except sqlite3.Error as e:
            log.debug("Could not read recent dictations: %s", e)
            return
        self._icon.update_menu()

    # -- Menu (main thread) -----------------------------------------------

    def _menu_items(self):
        # Sections: status, pausing, recent transcripts, windows, quit
        Item, Menu = self._pystray.MenuItem, self._pystray.Menu
        yield Item(self._status_line(), None, enabled=False)
        if self._key_problem():
            yield Item(SET_KEY, self._open_key_page)
        yield Menu.SEPARATOR
        yield Item("Pause Dictation", self._toggle_pause, checked=lambda _: self._paused)
        yield Menu.SEPARATOR
        yield Item(RECENT_HEADER if self._recent else "No dictations yet", None, enabled=False)
        for rec in self._recent:
            yield Item(("Partial: " if rec.status == "partial" else "") + _recent_label(rec.text), self._copier(rec.text))
        yield Menu.SEPARATOR
        yield Item("History…", self._open_history, enabled=self._history is not None)
        yield Item("Transcription", Menu(self._transcription_items))
        yield Item(SETTINGS, self._open_settings_item, enabled=lambda _: self._state is IconState.IDLE)
        yield Menu.SEPARATOR
        yield Item("Quit Vox Transfer", self._quit)

    def _copier(self, text: str):
        def action(icon, item):
            try:
                self._copy(text)
                log.info("Copied recent dictation to clipboard (%d chars)", len(text))
            except Exception as e:
                log.warning("Failed to copy dictation: %s", e)

        return action

    def _transcription_items(self):
        Item = self._pystray.MenuItem
        for mode, label in LABELS.items():
            yield Item(
                label, self._mode_setter(mode),
                checked=lambda _, mode=mode: self._config.mode == mode,
                enabled=lambda _, mode=mode: self._can_select_mode(mode),
                radio=True,
            )

    def _can_select_mode(self, mode: str) -> bool:
        # While Settings is open, its Transcription page owns the choice; until config.toml loads,
        # Vox refuses a switch
        if self._state is not IconState.IDLE or self._settings_open or self._config.config_error:
            return False
        # The current mode stays clickable: picking it again retries a setup that failed
        return mode == self._config.mode or mode_problem(self._config, mode) is None

    def _mode_setter(self, mode: str):
        def action(icon, item):
            self._send(f"mode:{mode}")

        return action

    def _toggle_pause(self, icon, item) -> None:
        self._send("resume" if self._paused else "pause")

    def _open_history(self, icon, item) -> None:
        if self._history is not None:
            # The window can delete dictations, so re-read the recent ones once it closes
            self._open_window(HISTORY_WINDOW, "--db", str(self._history.path), on_exit=self.history_changed)

    def _open_settings_item(self, icon, item) -> None:
        self._open_settings()

    def _open_key_page(self, icon, item) -> None:
        self._open_settings("transcription")

    def _open_settings(self, page: str | None = None) -> None:
        # The hotkey mustn't dictate while Settings is open: its Hotkey page records keys. settings:open goes before
        # the window starts, so settings:closed, which its exit sends however it ends, always comes after it. The
        # daemon then re-reads the API key and config.toml, so what the window saved applies at once.
        def opened() -> None:
            self._send("settings:open")
            self._apply_settings_open(True)

        def closed() -> None:
            self._send("settings:closed")
            self._dispatch(self._apply_settings_open, False)

        path = self._config.config_path or DEFAULT_CONFIG_PATH
        args = ["--config", str(path), *(["--page", page] if page else [])]
        self._open_window(SETTINGS_WINDOW, *args, on_open=opened, on_exit=closed)

    def _quit(self, icon, item) -> None:
        log.info("Quit requested from menu bar")
        self.request_quit()

    # -- Plumbing ----------------------------------------------------------

    def _open_window(
        self,
        module: str,
        *args: str,
        on_open: Callable[[], None] | None = None,
        on_exit: Callable[[], None] | None = None,
    ) -> None:
        """Start a window process, or bring it forward if it is open. ``on_open`` runs only when one starts,
        just before it does, and ``on_exit`` once it ends, or at once if it can't start."""
        # -P: a vox/ directory in the working directory must not shadow the installed package
        command = [sys.executable, "-P", "-m", module, *args]
        proc = self._windows.get(module)
        if proc is not None and proc.poll() is None:
            log.info("%s is already open; bringing it forward", module)
            self._focus(proc, command)
            return
        if on_open is not None:
            on_open()
        try:
            proc = self._windows[module] = self._launcher(command)
        except OSError as e:
            log.warning("Failed to open %s: %s", module, e)
            if on_exit is not None:
                on_exit()  # as if it closed at once
            return
        # Always wait on the window, so a closed one doesn't linger as a zombie
        threading.Thread(target=_call_after_exit, args=(proc, on_exit), name=f"{module}-exit", daemon=True).start()
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


def _call_after_exit(proc: subprocess.Popen, fn: Callable[[], None] | None) -> None:
    proc.wait()
    if fn is not None:
        fn()


def _reap(proc: subprocess.Popen) -> None:
    threading.Thread(target=proc.wait, name="window-reap", daemon=True).start()


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _recent_label(text: str) -> str:
    """Quoted one-line preview, so transcripts read as content rather than commands."""
    return f"“{_one_line(text, _RECENT_LABEL_CHARS)}”"
