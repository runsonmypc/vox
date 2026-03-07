"""Text injection via clipboard + xdotool paste simulation."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time

from .errors import DependencyError, InjectionError
from .window import AppType

log = logging.getLogger(__name__)


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
    """Inject text at cursor by saving clipboard, setting text, pasting, then restoring."""
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
