"""Tests for the vox command's startup checks."""

from unittest.mock import patch

import pytest

from vox import __main__ as cli
from vox.config import Config


def test_instance_lock_is_exclusive_until_released(tmp_path):
    path = tmp_path / "vox.lock"
    first = cli._acquire_instance_lock(path)
    assert first is not None
    assert cli._acquire_instance_lock(path) is None
    first.close()
    second = cli._acquire_instance_lock(path)
    assert second is not None
    second.close()


def test_second_instance_exits_cleanly_before_config_and_permission_prompt(monkeypatch):
    """A non-zero exit would make launchd/systemd treat it as a crash and retry every few seconds."""
    monkeypatch.setattr("sys.argv", ["vox"])
    with (
        patch.object(cli, "_acquire_instance_lock", return_value=None),
        patch("vox.config.load_config") as load_config,
        patch("vox.injector.check_accessibility_permission") as prompt,
    ):
        cli.main()  # returns instead of sys.exit(1)
    load_config.assert_not_called()
    prompt.assert_not_called()


def test_whisper_cpp_starts_without_openai_key(monkeypatch):
    monkeypatch.setattr("sys.argv", ["vox"])
    config = Config(mode="whisper_cpp", openai_api_key="")
    with (
        patch.object(cli, "_acquire_instance_lock", return_value=object()),
        patch("vox.config.load_config", return_value=config),
        patch("vox.injector.check_dependencies"),
        patch("vox.injector.check_accessibility_permission", return_value=True),
        patch("vox.whisper_cpp.WhisperCppTranscriber"),
        patch("vox.daemon.run") as run,
    ):
        cli.main()
    run.assert_called_once_with(config)


def test_whisper_cpp_reports_missing_model_before_starting(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["vox"])
    binary = tmp_path / "whisper-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    config = Config(mode="whisper_cpp", whisper_cpp_binary=str(binary), whisper_cpp_model="")
    with (
        patch.object(cli, "_acquire_instance_lock", return_value=object()),
        patch("vox.config.load_config", return_value=config),
        patch("vox.daemon.run") as run,
        pytest.raises(SystemExit) as exit_info,
    ):
        cli.main()
    assert exit_info.value.code == 1
    run.assert_not_called()
