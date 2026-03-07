"""Active window detection, app classification, and screen context via AT-SPI."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

from .config import Config

log = logging.getLogger(__name__)

# Initialize AT-SPI once
Atspi.init()


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

# Max chars of surrounding text to capture for context
_MAX_CONTEXT_CHARS = 500


@dataclass
class AppContext:
    """Context about the currently focused application."""
    wm_class: str
    window_title: str
    app_type: AppType
    surrounding_text: str = ""
    caret_offset: int = -1


def detect_active_window(config: Config) -> AppContext:
    """Detect the currently active window, classify it, and read surrounding text."""
    wm_class = ""
    title = ""

    try:
        win_id = subprocess.check_output(
            ["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL, text=True
        ).strip()

        title = subprocess.check_output(
            ["xdotool", "getactivewindow", "getwindowname"], stderr=subprocess.DEVNULL, text=True
        ).strip()

        # Get WM_CLASS via xprop
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

    # Read surrounding text via AT-SPI
    surrounding_text, caret_offset = _read_focused_text()

    ctx = AppContext(
        wm_class=wm_class,
        window_title=title,
        app_type=app_type,
        surrounding_text=surrounding_text,
        caret_offset=caret_offset,
    )
    log.debug(
        "Window: class=%r title=%r type=%s context=%d chars",
        wm_class, title, app_type.value, len(surrounding_text),
    )
    return ctx


def _read_focused_text() -> tuple[str, int]:
    """Walk the AT-SPI tree to find the focused text element and read its content."""
    try:
        desktop = Atspi.get_desktop(0)
        result = _find_focused_text(desktop, depth=0)
        if result:
            return result
    except Exception as e:
        log.debug("AT-SPI text read failed: %s", e)
    return "", -1


def _find_focused_text(obj, depth: int) -> tuple[str, int] | None:
    """Recursively find the focused element with a Text interface."""
    if depth > 12:
        return None
    try:
        n = obj.get_child_count()
    except Exception:
        return None

    for i in range(n):
        try:
            child = obj.get_child_at_index(i)
            if child is None:
                continue

            state_set = child.get_state_set()
            if state_set.contains(Atspi.StateType.FOCUSED):
                ifaces = child.get_interfaces()
                if "Text" in ifaces:
                    char_count = Atspi.Text.get_character_count(child)
                    if char_count > 0:
                        # Read text around the caret, up to _MAX_CONTEXT_CHARS
                        caret = Atspi.Text.get_caret_offset(child)
                        start = max(0, caret - _MAX_CONTEXT_CHARS // 2)
                        end = min(char_count, start + _MAX_CONTEXT_CHARS)
                        text = Atspi.Text.get_text(child, start, end)
                        return text, caret

            # Recurse into children
            result = _find_focused_text(child, depth + 1)
            if result:
                return result
        except Exception:
            continue
    return None


def _classify(wm_class: str, title: str, config: Config) -> AppType:
    """Classify window into an AppType."""
    wm_lower = wm_class.lower()

    # Check user overrides first
    for pattern, type_str in config.window_classes.items():
        if pattern.lower() in wm_lower:
            try:
                return AppType(type_str.upper())
            except ValueError:
                log.warning("Invalid app type in config: %r", type_str)

    # Check defaults
    for pattern, app_type in _DEFAULT_CLASSES.items():
        if pattern in wm_lower:
            return app_type

    # Special case: vim/nvim in terminal title -> EDITOR
    title_lower = title.lower()
    if any(indicator in title_lower for indicator in ("nvim", "vim ", "- vim", "neovim")):
        return AppType.EDITOR

    return AppType.OTHER
