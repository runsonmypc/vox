"""Active window detection, app classification, and screen context via AT-SPI + OCR."""

from __future__ import annotations

import asyncio
import logging
import os
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
    "telegram-desktop": AppType.CHAT,
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


@dataclass
class AppContext:
    """Context about the currently focused application."""
    wm_class: str
    window_title: str
    app_type: AppType
    screen_text: str = ""
    win_id: str = ""
    pid: str = ""


def _get_macos_window_title(app_name: str) -> str:
    """Retrieve window title via AppleScript with fallback to localized application name."""
    script = (
        'tell application "System Events"\n'
        '    try\n'
        '        set frontApp to first application process whose frontmost is true\n'
        '        tell frontApp\n'
        '            if (count of windows) > 0 then\n'
        '                return name of front window\n'
        '            end if\n'
        '        end tell\n'
        '    on error\n'
        '        return ""\n'
        '    end try\n'
        'end tell\n'
        'return ""'
    )
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=1,
        )
        title = res.stdout.strip()
        if title:
            return title
    except Exception as e:
        log.debug("Failed to get window title via AppleScript: %s", e)
    return app_name


def _detect_active_window_macos(config: Config) -> AppContext:
    """Detect frontmost application on macOS via Cocoa NSWorkspace."""
    app_name = ""
    bundle_id = ""
    pid = ""

    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is not None:
            app_name = app.localizedName() or ""
            bundle_id = app.bundleIdentifier() or ""
            pid = str(app.processIdentifier() or "")
    except Exception as e:
        log.warning("Failed to detect active window via NSWorkspace: %s", e)

    title = _get_macos_window_title(app_name)
    identifier = f"{bundle_id} {app_name}".strip() if bundle_id else app_name
    app_type = _classify(identifier, title, config)
    log.debug("macOS Window: bundle_id=%r app_name=%r title=%r type=%s", bundle_id, app_name, title, app_type.value)

    return AppContext(
        wm_class=identifier,
        window_title=title,
        app_type=app_type,
        win_id="",
        pid=pid,
    )


def _detect_active_window_linux(config: Config) -> AppContext:
    """Detect active window on Linux via xdotool and xprop."""
    wm_class = ""
    title = ""
    win_id = ""
    pid = ""

    try:
        # Single xdotool call: get window ID, name, and PID
        out = subprocess.check_output(
            ["xdotool", "getactivewindow", "getwindowname",
             "getactivewindow", "getwindowpid",
             "getactivewindow"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip().splitlines()
        if len(out) >= 3:
            title = out[0]
            pid = out[1]
            win_id = out[2]

        if win_id:
            xprop_out = subprocess.check_output(
                ["xprop", "-id", win_id, "WM_CLASS"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            if "=" in xprop_out:
                parts = xprop_out.split("=", 1)[1].strip()
                quoted = [s.strip().strip('"') for s in parts.split(",")]
                wm_class = quoted[-1] if quoted else ""

    except (subprocess.CalledProcessError, FileNotFoundError):
        log.warning("Failed to detect active window via xdotool")

    app_type = _classify(wm_class, title, config)
    log.debug("Window: class=%r title=%r type=%s", wm_class, title, app_type.value)
    return AppContext(
        wm_class=wm_class, window_title=title, app_type=app_type,
        win_id=win_id, pid=pid,
    )


def detect_active_window(config: Config) -> AppContext:
    """Detect the currently active window and classify it (no screen text yet)."""
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


def _read_vision_ocr() -> str:
    """Capture screen using screencapture and recognize text with Apple Vision framework."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        res = subprocess.run(
            ["screencapture", "-x", tmp_path],
            capture_output=True,
            timeout=5,
        )
        if res.returncode != 0 or not os.path.exists(tmp_path):
            return ""

        from Foundation import NSURL, NSDictionary
        import Vision

        ns_url = NSURL.fileURLWithPath_(tmp_path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(ns_url, NSDictionary.dictionary())
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
        request.setUsesLanguageCorrection_(True)
        success, _ = handler.performRequests_error_([request], None)
        if not success:
            return ""

        results = request.results()
        if not results:
            return ""

        lines = [obs.topCandidates_(1)[0].string() for obs in results if obs.topCandidates_(1)]
        full_text = " ".join(lines)
        return full_text[:_MAX_CONTEXT_CHARS]
    except Exception as e:
        log.debug("Vision OCR capture failed: %s", e)
        return ""
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _capture_screen_text(win_id: str, pid: str, app_type: AppType) -> str:
    """Capture screen text — platform-aware."""
    if sys.platform == "darwin":
        # Check tmux first if in terminal
        if app_type == AppType.TERMINAL:
            tmux_text = _read_tmux_pane()
            if tmux_text:
                log.debug("Screen text from tmux: %d chars", len(tmux_text))
                return tmux_text

        # Try Vision OCR
        vision_text = _read_vision_ocr()
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
        tmux_text = _read_tmux_pane()
        if tmux_text:
            log.debug("Screen text from tmux: %d chars", len(tmux_text))
            return tmux_text

    # AT-SPI might have returned something small (e.g. ghostty tab titles)
    if text:
        log.debug("Screen text from AT-SPI (partial): %d chars", len(text))
        return text

    return ""


def _has_ocr(*, _cache: dict[str, bool] = {}) -> bool:
    """Check (once) whether maim and tesseract are available."""
    if "v" not in _cache:
        _cache["v"] = bool(shutil.which("maim") and shutil.which("tesseract"))
    return _cache["v"]


def _read_atspi_text(pid: str) -> str:
    """Read AT-SPI text in a subprocess to isolate potential segfaults."""
    if not pid:
        return ""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "vox._atspi_reader", pid, str(_MAX_CONTEXT_CHARS)],
            capture_output=True, text=True, timeout=3,
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
    try:
        result = subprocess.run(
            f'maim -i {win_id} --format=png | tesseract stdin stdout 2>/dev/null',
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        text = result.stdout.strip()
        if text:
            return text[:_MAX_CONTEXT_CHARS]
    except Exception as e:
        log.debug("OCR failed: %s", e)
    return ""


def _read_tmux_pane() -> str:
    """Read the most recently active tmux pane's visible content."""
    if not shutil.which("tmux"):
        return ""
    try:
        text = subprocess.check_output(
            ["tmux", "capture-pane", "-p"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        return text.strip()[:_MAX_CONTEXT_CHARS]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _classify(wm_class: str, title: str, config: Config) -> AppType:
    """Classify window into an AppType."""
    wm_lower = wm_class.lower()

    for pattern, type_str in config.window_classes.items():
        if pattern.lower() in wm_lower:
            try:
                return AppType(type_str.upper())
            except ValueError:
                log.warning("Invalid app type in config: %r", type_str)

    for pattern, app_type in _DEFAULT_CLASSES.items():
        if pattern in wm_lower:
            return app_type

    title_lower = title.lower()
    if any(indicator in title_lower for indicator in ("nvim", "vim ", "- vim", "neovim")):
        return AppType.EDITOR

    return AppType.OTHER
