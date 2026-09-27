"""Audio attenuation via wpctl (Linux) or AppleScript (macOS)."""

from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger(__name__)

# Some macOS output devices (external DACs, AirPlay, HDMI, aggregate devices)
# are hardware-controlled and report "missing value" for output volume. That is
# not an error, it just means software attenuation is unavailable on that device.
_UNSUPPORTED_SENTINEL = "missing value"

# wpctl reports volumes above 1.0 when a sink is over-amplified (GNOME allows up to 150%).
# Restoring must give that level back, while a bad attenuation_level can never push past it.
_WPCTL_MAX_VOLUME = 1.5

# Whether we have already told the user attenuation is unavailable. Reset on a
# successful read so switching back to a supported device logs again if needed.
_unsupported_notified = False


def _get_volume_macos() -> float | None:
    """Get current system volume (0.0-1.0) on macOS via AppleScript.

    Returns None when the active output device does not support software volume.
    """
    global _unsupported_notified
    try:
        result = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            log.warning("AppleScript get volume failed: %s", result.stderr.strip())
            return None

        raw = result.stdout.strip()
        if raw == _UNSUPPORTED_SENTINEL:
            if not _unsupported_notified:
                log.info(
                    "Output device is hardware-controlled; volume attenuation unavailable"
                )
                _unsupported_notified = True
            else:
                log.debug("Output volume still unavailable on this device")
            return None

        try:
            vol = float(raw)
        except ValueError:
            log.warning("Unexpected AppleScript volume output: %r", raw)
            return None

        _unsupported_notified = False
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
    """Get current default sink volume via wpctl (0.0-1.5; above 1.0 when over-amplified)."""
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
    """Set default sink volume via wpctl (0.0-1.5, so an over-amplified level can be restored)."""
    level = max(0.0, min(_WPCTL_MAX_VOLUME, level))
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
    """Get current output volume (0.0-1.0, above 1.0 for an over-amplified Linux sink)."""
    if sys.platform == "darwin":
        return _get_volume_macos()
    return _get_volume_linux()


def set_volume(level: float) -> None:
    """Set output volume (0.0-1.0, above 1.0 for an over-amplified Linux sink)."""
    if sys.platform == "darwin":
        _set_volume_macos(level)
    else:
        _set_volume_linux(level)
