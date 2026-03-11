"""Text injection via clipboard + xdotool paste simulation."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time

from Xlib import XK, display as xdisplay
from Xlib.ext import xtest

from .errors import DependencyError, InjectionError
from .window import AppType

log = logging.getLogger(__name__)

# Xlib display for fast key injection (lazy init)
_display: xdisplay.Display | None = None


def _get_display() -> xdisplay.Display:
    global _display
    if _display is None:
        _display = xdisplay.Display()
    return _display


def _xlib_paste(d: xdisplay.Display) -> None:
    """Send Ctrl+V via XTest fake input events."""
    ctrl_keycode = d.keysym_to_keycode(XK.XK_Control_L)
    v_keycode = d.keysym_to_keycode(XK.XK_v)
    xtest.fake_input(d, xtest.X.KeyPress, ctrl_keycode)
    xtest.fake_input(d, xtest.X.KeyPress, v_keycode)
    xtest.fake_input(d, xtest.X.KeyRelease, v_keycode)
    xtest.fake_input(d, xtest.X.KeyRelease, ctrl_keycode)
    d.flush()


def check_dependencies() -> None:
    """Check that xdotool and xclip are installed."""
    missing = []
    if not shutil.which("xdotool"):
        missing.append("xdotool")
    if not shutil.which("xclip"):
        missing.append("xclip")
    if missing:
        raise DependencyError(
            f"Missing system dependencies: {', '.join(missing)}. "
            f"Install with: sudo apt install {' '.join(missing)}"
        )


def inject_text(text: str, app_type: AppType) -> None:
    """Inject text at cursor via clipboard paste, or xdotool type for games/unknown apps."""
    # Games and unknown apps: clipboard paste via XTest (xdotool's --clearmodifiers breaks it)
    if app_type == AppType.OTHER:
        try:
            proc = subprocess.Popen(
                ["xclip", "-selection", "clipboard"],
                stdin=subprocess.PIPE,
            )
            proc.communicate(input=text.encode())
            time.sleep(0.05)
            _xlib_paste(_get_display())
            log.debug("Injected %d chars via Xlib paste", len(text))
        except Exception as e:
            raise InjectionError(f"Xlib paste failed: {e}") from e
        return

    # Save current clipboard
    original_clipboard: str | None = None
    try:
        original_clipboard = subprocess.check_output(
            ["xclip", "-selection", "clipboard", "-o"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # clipboard empty or unavailable

    try:
        # Set our text to clipboard
        proc = subprocess.Popen(
            ["xclip", "-selection", "clipboard"],
            stdin=subprocess.PIPE,
        )
        proc.communicate(input=text.encode())
        if proc.returncode != 0:
            raise InjectionError("Failed to set clipboard via xclip")

        # Paste
        time.sleep(0.05)  # small delay for clipboard to settle
        if app_type == AppType.TERMINAL:
            paste_keys = "ctrl+shift+v"
        else:
            paste_keys = "ctrl+v"

        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", paste_keys],
            check=True,
            stderr=subprocess.DEVNULL,
        )
        log.debug("Injected %d chars via %s", len(text), paste_keys)

        # Wait for paste to complete
        time.sleep(0.05)
    except subprocess.CalledProcessError as e:
        raise InjectionError(f"xdotool paste failed: {e}") from e
    finally:
        # Restore original clipboard
        if original_clipboard is not None:
            try:
                proc = subprocess.Popen(
                    ["xclip", "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                )
                proc.communicate(input=original_clipboard.encode())
            except Exception:
                log.warning("Failed to restore clipboard")
