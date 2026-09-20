"""Tests for volume attenuation, especially macOS hardware-controlled outputs."""

from __future__ import annotations

import logging
import subprocess
from unittest.mock import patch

import vox.attenuation as attenuation
from vox.attenuation import _get_volume_macos


def _result(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_missing_value_returns_none_without_warning(caplog):
    """A hardware-controlled output reports 'missing value'; that is not a warning."""
    attenuation._unsupported_notified = False
    with patch("vox.attenuation.subprocess.run", return_value=_result("missing value\n")), \
         caplog.at_level(logging.DEBUG, logger="vox.attenuation"):
        assert _get_volume_macos() is None

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("attenuation unavailable" in r.message for r in caplog.records)


def test_missing_value_notifies_only_once(caplog):
    """Repeated unsupported reads must not spam the log at info level."""
    attenuation._unsupported_notified = False
    with patch("vox.attenuation.subprocess.run", return_value=_result("missing value\n")), \
         caplog.at_level(logging.DEBUG, logger="vox.attenuation"):
        _get_volume_macos()
        _get_volume_macos()
        _get_volume_macos()

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1


def test_recovers_after_device_change(caplog):
    """Switching back to a supported device yields a real reading and re-arms notification."""
    attenuation._unsupported_notified = True
    with patch("vox.attenuation.subprocess.run", return_value=_result("80\n")):
        assert _get_volume_macos() == 0.8
    assert attenuation._unsupported_notified is False


def test_normal_reading_is_clamped():
    with patch("vox.attenuation.subprocess.run", return_value=_result("150\n")):
        assert _get_volume_macos() == 1.0
    with patch("vox.attenuation.subprocess.run", return_value=_result("0\n")):
        assert _get_volume_macos() == 0.0


def test_unparseable_output_warns(caplog):
    """Genuinely unexpected output still deserves a warning."""
    attenuation._unsupported_notified = False
    with patch("vox.attenuation.subprocess.run", return_value=_result("banana\n")), \
         caplog.at_level(logging.DEBUG, logger="vox.attenuation"):
        assert _get_volume_macos() is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_nonzero_returncode_warns(caplog):
    attenuation._unsupported_notified = False
    with patch("vox.attenuation.subprocess.run", return_value=_result("", 1, "boom")), \
         caplog.at_level(logging.DEBUG, logger="vox.attenuation"):
        assert _get_volume_macos() is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
