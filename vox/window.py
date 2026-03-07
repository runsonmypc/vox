"""Active window detection, app classification, and screen context via AT-SPI + OCR."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
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


# Default WM_CLASS -> AppType mappings
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
    # Chat
    "slack": AppType.CHAT,
    "discord": AppType.CHAT,
    "telegram-desktop": AppType.CHAT,
    "signal": AppType.CHAT,
    "element": AppType.CHAT,
    # Email
    "thunderbird": AppType.EMAIL,
    "geary": AppType.EMAIL,
    "evolution": AppType.EMAIL,
    # Browsers
    "firefox": AppType.BROWSER,
    "google-chrome": AppType.BROWSER,
    "chromium-browser": AppType.BROWSER,
    "brave-browser": AppType.BROWSER,
}


@dataclass
class AppContext:
    """Context about the currently focused application."""
    wm_class: str
    window_title: str
    app_type: AppType
    screen_text: str = ""


def detect_active_window(config: Config) -> AppContext:
    """Detect the currently active window and classify it (no screen text yet)."""
    wm_class = ""
    title = ""
    win_id = ""
    pid = ""

    try:
        win_id = subprocess.check_output(
            ["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL, text=True
        ).strip()

        title = subprocess.check_output(
            ["xdotool", "getactivewindow", "getwindowname"], stderr=subprocess.DEVNULL, text=True
        ).strip()

        pid = subprocess.check_output(
            ["xdotool", "getwindowpid", win_id], stderr=subprocess.DEVNULL, text=True
        ).strip()

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

    ctx = AppContext(
        wm_class=wm_class,
        window_title=title,
        app_type=app_type,
    )
    ctx._win_id = win_id
    ctx._pid = pid
    log.debug("Window: class=%r title=%r type=%s", wm_class, title, app_type.value)
    return ctx


def start_screen_capture(ctx: AppContext) -> asyncio.Future:
    """Start screen text capture in background. Returns a future with the result.

    Call this when recording starts. By the time recording stops,
    the screen text will be ready.
    """
    loop = asyncio.get_running_loop()
    win_id = getattr(ctx, "_win_id", "")
    pid = getattr(ctx, "_pid", "")
    app_type = ctx.app_type
    return loop.run_in_executor(_ocr_pool, _capture_screen_text, win_id, pid, app_type)


def _capture_screen_text(win_id: str, pid: str, app_type: AppType) -> str:
    """Capture screen text — tries AT-SPI, then OCR, then tmux. Runs in thread."""
    # Try AT-SPI first (fast, ~10ms)
    text = _read_atspi_text(pid)
    if text and len(text) > 100:
        log.debug("Screen text from AT-SPI: %d chars", len(text))
        return text

    # Try OCR (slower, ~1-2s, but works for everything)
    if win_id and shutil.which("maim") and shutil.which("tesseract"):
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
    except (subprocess.TimeoutExpired, Exception) as e:
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
