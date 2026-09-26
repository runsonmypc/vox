"""Tests for the vox command's startup checks."""

import os
from unittest.mock import patch

import pytest

import vox.daemon  # noqa: F401  imported now, so patch("vox.daemon.run") can't import it with a mocked WhisperCppTranscriber
from vox import __main__ as cli
from vox import keystore
from vox.config import Config

KEY = "sk-test-dummy-0001"


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


def start(config, monkeypatch):
    """Run the vox command up to the daemon with ``config``, returning the mocked daemon.run."""
    monkeypatch.setattr("sys.argv", ["vox"])
    with (
        patch.object(cli, "_acquire_instance_lock", return_value=object()),
        patch("vox.config.load_config", return_value=config),
        patch("vox.injector.check_dependencies"),
        patch("vox.injector.check_accessibility_permission", return_value=True),
        patch("vox.daemon.run") as run,
    ):
        cli.main()
    return run


def test_starts_without_a_key_instead_of_exiting(monkeypatch):
    """Exiting would make launchd/systemd restart Vox every few seconds; the menu asks for the key instead."""
    config = Config(mode="batch")
    run = start(config, monkeypatch)
    run.assert_called_once_with(config)
    assert config.openai_api_key == ""
    assert config.api_key_error is None


def test_key_comes_from_the_keychain(memory_keyring, monkeypatch):
    keystore.set_api_key(KEY)
    config = Config()
    start(config, monkeypatch)
    assert config.openai_api_key == KEY


def test_plain_text_key_moves_into_the_keychain_before_it_is_read(memory_keyring, monkeypatch):
    keystore.FALLBACK_PATH.write_text(f"OPENAI_API_KEY={KEY}\n")
    config = Config()
    start(config, monkeypatch)
    assert config.openai_api_key == KEY
    assert memory_keyring.get_password(keystore.SERVICE, keystore.USERNAME) == KEY
    assert not keystore.FALLBACK_PATH.exists()


def test_unreadable_keychain_is_remembered_rather_than_treated_as_no_key(memory_keyring, monkeypatch):
    def refuse(*_args):
        raise RuntimeError("Failed to unlock the collection!")

    monkeypatch.setattr(memory_keyring, "get_password", refuse)
    config = Config()
    start(config, monkeypatch).assert_called_once_with(config)
    assert config.openai_api_key == ""
    assert config.api_key_error == "Failed to unlock the collection!"


def test_environment_key_is_used_but_hidden_from_processes_vox_starts(monkeypatch):
    monkeypatch.setattr(keystore, "_env_key", "")
    with patch.dict(os.environ, {"OPENAI_API_KEY": KEY}):
        config = Config()
        start(config, monkeypatch)
        assert config.openai_api_key == KEY
        assert "OPENAI_API_KEY" not in os.environ
        assert os.environ[keystore.OVERRIDE_FLAG] == "1"
