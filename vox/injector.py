"""Text injection via clipboard + xdotool paste simulation."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time

# Xlib is Linux-only; avoid failing on macOS where Xlib is not installed
if sys.platform != "darwin":
    try:
        from Xlib import XK, display as xdisplay
        from Xlib.ext import xtest
    except ImportError:
        pass

from .errors import DependencyError, InjectionError
from .window import AppType

log = logging.getLogger(__name__)

# Xlib display for fast key injection (lazy init)
_display = None


def _get_display():
    global _display
    if _display is None:
        from Xlib import display as xdisplay
        _display = xdisplay.Display()
    return _display


def _xlib_paste(d) -> None:
    """Send Ctrl+V via XTest fake input events."""
    from Xlib import XK
    from Xlib.ext import xtest
    ctrl_keycode = d.keysym_to_keycode(XK.XK_Control_L)
    v_keycode = d.keysym_to_keycode(XK.XK_v)
    xtest.fake_input(d, xtest.X.KeyPress, ctrl_keycode)
    xtest.fake_input(d, xtest.X.KeyPress, v_keycode)
    xtest.fake_input(d, xtest.X.KeyRelease, v_keycode)
    xtest.fake_input(d, xtest.X.KeyRelease, ctrl_keycode)
    d.flush()


def check_accessibility_permission(prompt: bool = False) -> bool:
    """Check if current process has macOS Accessibility permissions via AXIsProcessTrusted."""
    if sys.platform != "darwin":
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        if prompt:
            return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
        return bool(AXIsProcessTrusted())
    except ImportError:
        try:
            import ctypes
            app_services = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            return bool(app_services.AXIsProcessTrusted())
        except Exception:
            return True


def check_dependencies() -> None:
    """Check that required system dependencies are installed."""
    if sys.platform == "darwin":
        if not shutil.which("osascript"):
            raise DependencyError("Missing system dependency: osascript")
        return

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


_keyboard_controller = None


def _get_keyboard_controller():
    global _keyboard_controller
    if _keyboard_controller is None:
        from pynput.keyboard import Controller
        _keyboard_controller = Controller()
    return _keyboard_controller


def _get_clipboard() -> str | None:
    """Get current clipboard contents as a string."""
    if sys.platform == "darwin":
        try:
            from AppKit import NSPasteboard, NSPasteboardTypeString
            pb = NSPasteboard.generalPasteboard()
            return pb.stringForType_(NSPasteboardTypeString)
        except Exception as e:
            log.warning("Failed to read NSPasteboard: %s", e)
            return None
    try:
        return subprocess.check_output(
            ["xclip", "-selection", "clipboard", "-o"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _set_clipboard(text: str) -> None:
    """Set clipboard contents to string."""
    if sys.platform == "darwin":
        try:
            from AppKit import NSPasteboard, NSPasteboardTypeString
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(text, NSPasteboardTypeString)
            return
        except Exception as e:
            raise InjectionError(f"Failed to set NSPasteboard: {e}") from e

    proc = subprocess.Popen(
        ["xclip", "-selection", "clipboard"],
        stdin=subprocess.PIPE,
    )
    proc.communicate(input=text.encode())
    if proc.returncode != 0:
        raise InjectionError("Failed to set clipboard via xclip")


def _simulate_paste_macos() -> None:
    """Simulate Cmd+V paste using pynput.keyboard.Controller."""
    try:
        from pynput.keyboard import Key
        ctrl = _get_keyboard_controller()
        with ctrl.pressed(Key.cmd):
            ctrl.press("v")
            ctrl.release("v")
    except Exception as e:
        raise InjectionError(f"macOS paste simulation failed: {e}") from e


def _inject_text_macos(text: str, app_type: AppType) -> None:
    """Inject text on macOS using NSPasteboard and Cmd+V."""
    original_clipboard = _get_clipboard()
    try:
        _set_clipboard(text)
        time.sleep(0.05)
        _simulate_paste_macos()
        log.debug("Injected %d chars via Cmd+V on macOS (app_type=%s)", len(text), app_type.value)
        time.sleep(0.05)
    except Exception as e:
        if isinstance(e, InjectionError):
            raise
        raise InjectionError(f"macOS text injection failed: {e}") from e
    finally:
        if original_clipboard is not None:
            try:
                _set_clipboard(original_clipboard)
            except Exception:
                log.warning("Failed to restore clipboard")


def _inject_text_linux(text: str, app_type: AppType) -> None:
    """Inject text on Linux via xclip and xdotool / XTest."""
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


def inject_text(text: str, app_type: AppType) -> None:
    """Inject text at cursor via clipboard paste."""
    if sys.platform == "darwin":
        _inject_text_macos(text, app_type)
    else:
        _inject_text_linux(text, app_type)


def paste(text: str, app_type: AppType = AppType.OTHER) -> None:
    """Single-shot clipboard paste injection."""
    inject_text(text, app_type)
