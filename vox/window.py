"""Active window detection, app classification, and screen context of the focused window."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum

from .config import Config

log = logging.getLogger(__name__)

# Max total chars of screen text to capture
_MAX_CONTEXT_CHARS = 2000

# Thread pool for OCR (runs in background during recording)
_ocr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")

# Timeout for the quick X11/tmux/ps queries
_TOOL_TIMEOUT = 2.0


class AppType(Enum):
    TERMINAL = "TERMINAL"
    EDITOR = "EDITOR"
    CHAT = "CHAT"
    EMAIL = "EMAIL"
    BROWSER = "BROWSER"
    OTHER = "OTHER"


# Default WM_CLASS / bundle identifier / app name -> AppType mappings
_DEFAULT_CLASSES: dict[str, AppType] = {
    # Terminals
    "ghostty": AppType.TERMINAL,
    "kitty": AppType.TERMINAL,
    "alacritty": AppType.TERMINAL,
    "gnome-terminal": AppType.TERMINAL,
    "xterm": AppType.TERMINAL,
    "konsole": AppType.TERMINAL,
    "tilix": AppType.TERMINAL,
    "wezterm": AppType.TERMINAL,
    "terminal": AppType.TERMINAL,
    "terminator": AppType.TERMINAL,
    "org.gnome.console": AppType.TERMINAL,
    "kgx": AppType.TERMINAL,
    "ptyxis": AppType.TERMINAL,
    "rxvt": AppType.TERMINAL,
    "st-256color": AppType.TERMINAL,
    "terminology": AppType.TERMINAL,
    "tilda": AppType.TERMINAL,
    "guake": AppType.TERMINAL,
    "cool-retro-term": AppType.TERMINAL,
    "iterm": AppType.TERMINAL,
    "com.mitchellh.ghostty": AppType.TERMINAL,
    "com.googlecode.iterm2": AppType.TERMINAL,
    "com.apple.terminal": AppType.TERMINAL,
    "net.kovidgoyal.kitty": AppType.TERMINAL,
    "io.alacritty": AppType.TERMINAL,
    # Editors
    "code": AppType.EDITOR,
    "vscodium": AppType.EDITOR,
    "jetbrains-idea": AppType.EDITOR,
    "jetbrains-pycharm": AppType.EDITOR,
    "jetbrains-webstorm": AppType.EDITOR,
    "sublime_text": AppType.EDITOR,
    "gedit": AppType.EDITOR,
    "zed": AppType.EDITOR,
    "cursor": AppType.EDITOR,
    "com.microsoft.vscode": AppType.EDITOR,
    "com.microsoft.vscodeinsiders": AppType.EDITOR,
    "com.todesktop.230313mzl4w4u92": AppType.EDITOR,
    "dev.zed.zed": AppType.EDITOR,
    "com.sublimetext": AppType.EDITOR,
    # Chat
    "slack": AppType.CHAT,
    "discord": AppType.CHAT,
    "telegram": AppType.CHAT,
    "signal": AppType.CHAT,
    "element": AppType.CHAT,
    "com.tinyspeck.slackmacgap": AppType.CHAT,
    "com.hnc.discord": AppType.CHAT,
    "ru.keepcoder.telegram": AppType.CHAT,
    "org.whispersystems.signal-desktop": AppType.CHAT,
    # Email
    "thunderbird": AppType.EMAIL,
    "geary": AppType.EMAIL,
    "evolution": AppType.EMAIL,
    "com.apple.mail": AppType.EMAIL,
    "org.mozilla.thunderbird": AppType.EMAIL,
    # Browsers
    "firefox": AppType.BROWSER,
    "google-chrome": AppType.BROWSER,
    "chromium-browser": AppType.BROWSER,
    "brave-browser": AppType.BROWSER,
    "safari": AppType.BROWSER,
    "com.apple.safari": AppType.BROWSER,
    "org.mozilla.firefox": AppType.BROWSER,
    "com.google.chrome": AppType.BROWSER,
    "com.brave.browser": AppType.BROWSER,
    "company.thebrowser.browser": AppType.BROWSER,
}

# Longest pattern first, so a full bundle id beats a short pattern inside it ("code" in "ru.keepcoder.telegram")
_DEFAULT_PATTERNS = sorted(_DEFAULT_CLASSES.items(), key=lambda item: -len(item[0]))


@dataclass
class AppContext:
    """Context about the currently focused application."""
    wm_class: str
    window_title: str
    app_type: AppType
    screen_text: str = ""
    win_id: str = ""  # X11 window id, or the macOS CGWindowID of the focused window
    pid: str = ""


def _is_number(value: str) -> bool:
    return re.fullmatch(r"[0-9]+", value) is not None


def _run_tool(args: list[str], timeout: float = _TOOL_TIMEOUT, latin1_fallback: bool = False) -> str | None:
    """A local tool's stdout, decoded leniently; None when it fails, hangs or is missing.

    ``latin1_fallback`` reads output that is not UTF-8 as Latin-1, as a legacy X client's
    STRING-typed WM_NAME is, instead of replacing its accented letters.
    """
    try:
        result = subprocess.run(args, capture_output=True, check=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        log.debug("%s failed: %s", args[0], e)
        return None
    if latin1_fallback:
        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return result.stdout.decode("latin-1")
    return result.stdout.decode("utf-8", errors="replace")


def _frontmost_window(pid: int) -> tuple[str, str]:
    """(CGWindowID, title) of the app's frontmost normal window, which is its focused one."""
    try:
        from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListExcludeDesktopElements, kCGWindowListOptionOnScreenOnly

        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, kCGNullWindowID,
        ) or ()
    except Exception as e:
        log.debug("Failed to list windows via Quartz: %s", e)
        return "", ""
    # Front to back. Layer 0 skips menu bar extras, panels and overlays.
    for w in windows:
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0 and w.get("kCGWindowAlpha", 1) > 0:
            # The name is only visible with Screen Recording permission
            return str(w.get("kCGWindowNumber", "")), str(w.get("kCGWindowName") or "").strip()
    return "", ""


def _ax_focused_window_title(pid: int) -> str:
    """The focused window's title via Accessibility, which Vox already needs for pasting."""
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementSetMessagingTimeout,
            kAXErrorSuccess,
            kAXFocusedWindowAttribute,
            kAXTitleAttribute,
        )

        app = AXUIElementCreateApplication(pid)
        # A hung app must not stall the dictation
        AXUIElementSetMessagingTimeout(app, 0.25)
        err, window = AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute, None)
        if err != kAXErrorSuccess or window is None:
            return ""
        AXUIElementSetMessagingTimeout(window, 0.25)
        err, title = AXUIElementCopyAttributeValue(window, kAXTitleAttribute, None)
        return str(title).strip() if err == kAXErrorSuccess and title else ""
    except Exception as e:
        log.debug("Failed to read the window title via Accessibility: %s", e)
        return ""


def _detect_active_window_macos(config: Config) -> AppContext:
    """Detect frontmost application on macOS via Cocoa NSWorkspace."""
    import objc

    app_name = ""
    bundle_id = ""
    pid = ""
    win_id = ""
    title = ""

    # Callers may be worker threads, which have no autorelease pool of their own
    with objc.autorelease_pool():
        try:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is not None:
                app_name = app.localizedName() or ""
                bundle_id = app.bundleIdentifier() or ""
                raw_pid = app.processIdentifier()
                if raw_pid and raw_pid > 0:
                    pid = str(raw_pid)
        except Exception as e:
            log.warning("Failed to detect active window via NSWorkspace: %s", e)

        if pid:
            win_id, title = _frontmost_window(int(pid))
            title = title or _ax_focused_window_title(int(pid))

    title = title or app_name
    identifier = f"{bundle_id} {app_name}".strip() if bundle_id else app_name
    app_type = _classify(identifier, title, config)
    log.debug("macOS Window: bundle_id=%r app_name=%r title=%r type=%s", bundle_id, app_name, title, app_type.value)

    return AppContext(
        wm_class=identifier,
        window_title=title,
        app_type=app_type,
        win_id=win_id,
        pid=pid,
    )


def _parse_xprop(output: str) -> tuple[str, str]:
    """(WM_CLASS class name, _NET_WM_PID) from `xprop -id ID WM_CLASS _NET_WM_PID` output."""
    wm_class = ""
    pid = ""
    for line in output.splitlines():
        name, sep, value = line.partition(" = ")
        if not sep:
            continue  # "WM_CLASS:  not found."
        if name.startswith("WM_CLASS("):
            quoted = re.findall(r'"([^"]*)"', value)
            wm_class = quoted[-1] if quoted else ""
        elif name.startswith("_NET_WM_PID(") and _is_number(value.strip()):
            pid = value.strip()
    return wm_class, pid


def _detect_active_window_linux(config: Config) -> AppContext:
    """Detect active window on Linux via xdotool and xprop."""
    wm_class = ""
    title = ""
    win_id = ""
    pid = ""

    active = _run_tool(["xdotool", "getactivewindow"])
    if active is None:
        log.warning("Failed to detect active window via xdotool")
    elif _is_number(active.strip()):
        win_id = active.strip()
        # One query per field, keyed by the window id: a title can contain line breaks,
        # so it must never be parsed by its position in shared output
        wm_class, pid = _parse_xprop(_run_tool(["xprop", "-id", win_id, "WM_CLASS", "_NET_WM_PID"]) or "")
        title = (_run_tool(["xdotool", "getwindowname", win_id], latin1_fallback=True) or "").removesuffix("\n")

    app_type = _classify(wm_class, title, config)
    log.debug("Window: class=%r title=%r type=%s", wm_class, title, app_type.value)
    return AppContext(
        wm_class=wm_class, window_title=title, app_type=app_type,
        win_id=win_id, pid=pid,
    )


def detect_active_window(config: Config) -> AppContext:
    """Detect the currently active window and classify it (no screen text yet). Safe from any thread."""
    if sys.platform == "darwin":
        return _detect_active_window_macos(config)
    return _detect_active_window_linux(config)


def start_screen_capture(ctx: AppContext) -> asyncio.Future:
    """Start screen text capture in background. Returns a future with the result.

    Call this when recording starts. By the time recording stops,
    the screen text will be ready.
    """
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_ocr_pool, _capture_screen_text, ctx.win_id, ctx.pid, ctx.app_type)


def _read_vision_ocr(win_id: str) -> str:
    """Capture the focused window (never the whole screen) and recognize its text with Apple Vision."""
    if not _is_number(win_id):
        return ""
    import objc

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        res = subprocess.run(
            ["screencapture", "-x", "-o", "-l", win_id, tmp_path],
            capture_output=True,
            timeout=5,
        )
        if res.returncode != 0 or not os.path.getsize(tmp_path):
            return ""

        import Vision
        from Foundation import NSURL, NSDictionary

        with objc.autorelease_pool():
            ns_url = NSURL.fileURLWithPath_(tmp_path)
            handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(ns_url, NSDictionary.dictionary())
            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
            # The text only feeds vocabulary hints; correction costs ~3x the time and rewrites identifiers
            request.setUsesLanguageCorrection_(False)
            success, _ = handler.performRequests_error_([request], None)
            if not success:
                return ""

            results = request.results()
            if not results:
                return ""

            lines = [obs.topCandidates_(1)[0].string() for obs in results if obs.topCandidates_(1)]
            # One line per recognized line, like tmux and tesseract: the secret filter drops a
            # password-like value up to the end of its line, which must not be the whole window
            return "\n".join(lines)[:_MAX_CONTEXT_CHARS]
    except Exception as e:
        log.debug("Vision OCR capture failed: %s", e)
        return ""
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _capture_screen_text(win_id: str, pid: str, app_type: AppType) -> str:
    """Capture the focused window's text, platform-aware."""
    if sys.platform == "darwin":
        # Check tmux first if in terminal
        if app_type == AppType.TERMINAL:
            tmux_text = _read_tmux_pane(pid)
            if tmux_text:
                log.debug("Screen text from tmux: %d chars", len(tmux_text))
                return tmux_text

        # Try Vision OCR
        vision_text = _read_vision_ocr(win_id)
        if vision_text:
            log.debug("Screen text from Vision OCR: %d chars", len(vision_text))
            return vision_text

        return ""

    # Linux flow:
    # Try AT-SPI first (fast, ~10ms)
    text = _read_atspi_text(pid)
    if text and len(text) > 100:
        log.debug("Screen text from AT-SPI: %d chars", len(text))
        return text

    # Try OCR (slower, ~1-2s, but works for everything)
    if win_id and _has_ocr():
        ocr_text = _read_ocr(win_id)
        if ocr_text:
            log.debug("Screen text from OCR: %d chars", len(ocr_text))
            return ocr_text

    # Tmux fallback for terminals
    if app_type == AppType.TERMINAL:
        tmux_text = _read_tmux_pane(pid)
        if tmux_text:
            log.debug("Screen text from tmux: %d chars", len(tmux_text))
            return tmux_text

    # AT-SPI might have returned something small (e.g. ghostty tab titles)
    if text:
        log.debug("Screen text from AT-SPI (partial): %d chars", len(text))
        return text

    return ""


@functools.cache
def _has_ocr() -> bool:
    """Check (once) whether maim and tesseract are available."""
    return bool(shutil.which("maim") and shutil.which("tesseract"))


def _read_atspi_text(pid: str) -> str:
    """Read the focused window's AT-SPI text in a subprocess to isolate potential segfaults."""
    if not _is_number(pid):
        return ""
    try:
        result = subprocess.run(
            # -P: never import a planted vox/ from the daemon's working directory
            [sys.executable, "-P", "-m", "vox._atspi_reader", pid, str(_MAX_CONTEXT_CHARS)],
            capture_output=True, text=True, errors="replace", timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.debug("AT-SPI subprocess timed out")
    except Exception as e:
        log.debug("AT-SPI read failed: %s", e)
    return ""


def _read_ocr(win_id: str) -> str:
    """Screenshot the window and OCR it with tesseract."""
    if not _is_number(win_id):
        return ""
    try:
        shot = subprocess.run(["maim", "-i", win_id, "--format=png"], capture_output=True, check=True, timeout=5)
        result = subprocess.run(["tesseract", "stdin", "stdout"], input=shot.stdout, capture_output=True, check=True, timeout=10)
    except (subprocess.SubprocessError, OSError) as e:
        log.debug("OCR failed: %s", e)
        return ""
    return result.stdout.decode("utf-8", errors="replace").strip()[:_MAX_CONTEXT_CHARS]


def _process_table() -> dict[str, tuple[str, str]]:
    """pid -> (parent pid, controlling tty) for every process, from one ps call."""
    table = {}
    for line in (_run_tool(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "tty="]) or "").splitlines():
        fields = line.split()
        if len(fields) == 3:
            table[fields[0]] = (fields[1], fields[2])
    return table


def _descendants(ancestor: str, table: dict[str, tuple[str, str]]) -> set[str]:
    children: dict[str, list[str]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    found: set[str] = set()
    pending = [ancestor]
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in found:  # ends even if the table has a cycle
                found.add(child)
                pending.append(child)
    return found


def _read_tmux_pane(terminal_pid: str) -> str:
    """The visible pane of the tmux client running inside the focused terminal.

    A bare `capture-pane` reads whichever pane was used last, which may be a background session
    the user is not looking at, so only a client descended from the terminal's process counts.
    Every window and tab of a terminal app shares that process, so the pane is read only when the
    app has no other session: then the tmux client is in the focused window.
    """
    if not _is_number(terminal_pid) or not shutil.which("tmux"):
        return ""
    clients = _run_tool(["tmux", "list-clients", "-F", "#{client_pid} #{client_tty} #{pane_id}"])
    if not clients:
        return ""
    table = _process_table()
    inside = _descendants(terminal_pid, table)
    found = {
        (fields[1].removeprefix("/dev/"), fields[2])
        for fields in map(str.split, clients.splitlines())
        if len(fields) == 3 and fields[0] in inside
    }
    if len(found) != 1:
        # None, or several tmux clients in this terminal app and no way to tell which one is focused
        return ""
    ((client_tty, pane),) = found
    # ps shows no controlling terminal as "?" on Linux and "??" on macOS
    session_ttys = {table[pid][1] for pid in inside} - {"?", "??"}
    if session_ttys != {client_tty}:
        # Another window or tab of the app, which may be the focused one; macOS OCRs that window instead
        return ""
    text = _run_tool(["tmux", "capture-pane", "-p", "-t", pane])
    return text.strip()[:_MAX_CONTEXT_CHARS] if text else ""


def _classify(wm_class: str, title: str, config: Config) -> AppType:
    """Classify window into an AppType."""
    wm_lower = wm_class.lower()

    for pattern, type_str in config.window_classes.items():
        if str(pattern).lower() in wm_lower:
            try:
                return AppType(str(type_str).upper())
            except ValueError:
                log.warning("Invalid app type in config: %r", type_str)

    for pattern, app_type in _DEFAULT_PATTERNS:
        if pattern in wm_lower:
            return app_type

    title_lower = title.lower()
    if any(indicator in title_lower for indicator in ("nvim", "vim ", "- vim", "neovim")):
        return AppType.EDITOR

    return AppType.OTHER
