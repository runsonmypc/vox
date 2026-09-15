"""Audio attenuation via wpctl (Linux) or AppleScript (macOS)."""

from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger(__name__)


def _get_volume_macos() -> float | None:
    """Get current system volume (0.0-1.0) on macOS via AppleScript."""
    try:
        result = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            log.warning("AppleScript get volume failed: %s", result.stderr.strip())
            return None
        vol = float(result.stdout.strip())
        return max(0.0, min(1.0, vol / 100.0))
    except Exception as e:
        log.warning("Failed to get macOS volume: %s", e)
        return None


def _set_volume_macos(level: float) -> None:
    """Set system volume (0.0-1.0) on macOS via AppleScript."""
    level = max(0.0, min(1.0, level))
    vol_int = int(round(level * 100))
    try:
        result = subprocess.run(
            ["osascript", "-e", f"set volume output volume {vol_int}"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            log.warning("AppleScript set volume failed: %s", result.stderr.strip())
    except Exception as e:
        log.warning("Failed to set macOS volume: %s", e)


def _get_volume_linux() -> float | None:
    """Get current default sink volume (0.0-1.0) via wpctl."""
    try:
        result = subprocess.run(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            log.warning("wpctl get-volume failed: %s", result.stderr.strip())
            return None
        # Output: "Volume: 0.31" or "Volume: 0.31 [MUTED]"
        parts = result.stdout.strip().split()
        if len(parts) >= 2 and parts[0] == "Volume:":
            return float(parts[1])
        log.warning("Unexpected wpctl output: %s", result.stdout.strip())
        return None
    except FileNotFoundError:
        log.warning("wpctl not found, attenuation unavailable")
        return None
    except Exception as e:
        log.warning("Failed to get volume: %s", e)
        return None


def _set_volume_linux(level: float) -> None:
    """Set default sink volume (0.0-1.0) via wpctl."""
    level = max(0.0, min(1.0, level))
    try:
        result = subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", str(round(level, 4))],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            log.warning("wpctl set-volume failed: %s", result.stderr.strip())
    except Exception as e:
        log.warning("Failed to set volume: %s", e)


def get_volume() -> float | None:
    """Get current output volume (0.0-1.0)."""
    if sys.platform == "darwin":
        return _get_volume_macos()
    return _get_volume_linux()


def set_volume(level: float) -> None:
    """Set output volume (0.0-1.0)."""
    if sys.platform == "darwin":
        _set_volume_macos(level)
    else:
        _set_volume_linux(level)
