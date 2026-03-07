"""Active window detection, app classification, and screen context via AT-SPI."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

from .config import Config

log = logging.getLogger(__name__)

# Initialize AT-SPI once
Atspi.init()

# Max total chars of screen text to capture
_MAX_CONTEXT_CHARS = 2000


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
    """Detect the currently active window, classify it, and read screen text."""
    wm_class = ""
    title = ""
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

    # Read screen text: try AT-SPI first, tmux fallback for terminals
    screen_text = _read_atspi_text(pid)
    if not screen_text and app_type == AppType.TERMINAL:
        screen_text = _read_tmux_pane()

    ctx = AppContext(
        wm_class=wm_class,
        window_title=title,
        app_type=app_type,
        screen_text=screen_text,
    )
    log.debug(
        "Window: class=%r title=%r type=%s screen_text=%d chars",
        wm_class, title, app_type.value, len(screen_text),
    )
    return ctx


def _read_atspi_text(pid: str) -> str:
    """Read all text from the AT-SPI app matching the given PID."""
    if not pid:
        return ""
    try:
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            try:
                if str(app.get_process_id()) == pid:
                    chunks: list[str] = []
                    _collect_text(app, chunks, depth=0)
                    text = "\n".join(chunks)
                    if len(text) > _MAX_CONTEXT_CHARS:
                        text = text[:_MAX_CONTEXT_CHARS]
                    return text
            except Exception:
                continue
    except Exception as e:
        log.debug("AT-SPI read failed: %s", e)
    return ""


def _collect_text(obj, chunks: list[str], depth: int) -> None:
    """Recursively collect text content from an AT-SPI accessible tree."""
    if depth > 20 or len(chunks) > 200:
        return
    try:
        n = obj.get_child_count()
    except Exception:
        return
    for i in range(n):
        try:
            child = obj.get_child_at_index(i)
            if child is None:
                continue
            ifaces = child.get_interfaces()
            if "Text" in ifaces:
                cc = Atspi.Text.get_character_count(child)
                if cc > 0:
                    text = Atspi.Text.get_text(child, 0, min(cc, 500))
                    # Skip placeholder-only text
                    cleaned = text.strip().replace("\ufffc", "").replace("\ufffd", "").strip()
                    if cleaned:
                        chunks.append(cleaned)
            _collect_text(child, chunks, depth + 1)
        except Exception:
            continue


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
