"""Audio attenuation via wpctl (PipeWire/WirePlumber)."""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)


def get_volume() -> float | None:
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


def set_volume(level: float) -> None:
    """Set default sink volume (0.0-1.0) via wpctl."""
    level = max(0.0, min(1.0, level))
    try:
        subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", str(round(level, 4))],
            capture_output=True, timeout=2,
        )
    except Exception as e:
        log.warning("Failed to set volume: %s", e)
