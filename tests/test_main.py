"""Tests for the vox command's startup checks."""

import importlib.metadata
import os
import stat
from unittest.mock import patch

import pytest

import vox.daemon  # noqa: F401  imported now, so patch("vox.daemon.run") can't import it with a mocked WhisperCppTranscriber
from vox import __main__ as cli
from vox import keystore
from vox.config import Config

KEY = "sk-test-dummy-0001"


def test_instance_lock_is_exclusive_until_released(tmp_path):
    lock_dir = tmp_path / "vox"
    first = cli._acquire_instance_lock(lock_dir)
    assert first is not None
    assert cli._acquire_instance_lock(lock_dir) is None
    os.close(first)
    second = cli._acquire_instance_lock(lock_dir)
    assert second is not None
    os.close(second)


def test_lock_directory_is_created_owner_only(tmp_path):
    lock_dir = tmp_path / "state" / "vox"
    os.close(cli._acquire_instance_lock(lock_dir))
    assert stat.S_IMODE(lock_dir.stat().st_mode) == 0o700


def test_deleting_files_in_the_lock_directory_does_not_admit_a_second_instance(tmp_path):
    """The old lock file in /tmp was deleted under a running Vox, and the next start ran a second one."""
    lock_dir = tmp_path / "vox"
    first = cli._acquire_instance_lock(lock_dir)
    (lock_dir / "daemon.lock").touch()
    (lock_dir / "daemon.lock").unlink()
    assert cli._acquire_instance_lock(lock_dir) is None
    os.close(first)


@pytest.mark.parametrize("platform, runtime_dir, expected", [
    ("linux", "RUNTIME", "RUNTIME/vox"),
    ("linux", None, "HOME/.local/state/vox"),
    ("linux", "relative/dir", "HOME/.local/state/vox"),
    ("darwin", "RUNTIME", "HOME/.local/state/vox"),
])
def test_lock_directory_is_per_user_and_outside_tmp(tmp_path, monkeypatch, platform, runtime_dir, expected):
    home, runtime = tmp_path / "home", tmp_path / "run-user-501"
    runtime.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli.sys, "platform", platform)
    monkeypatch.setattr(cli.os, "getuid", lambda: 999_999)  # no real /run/user/999999
    if runtime_dir is None:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    else:
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime_dir.replace("RUNTIME", str(runtime)))
    assert str(cli._lock_dir()) == expected.replace("RUNTIME", str(runtime)).replace("HOME", str(home))


def test_a_lock_that_cannot_be_made_is_an_error_not_another_instance(tmp_path, monkeypatch):
    """Before, any error opening the lock was reported as 'already running' with exit 0, so nothing retried."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o500)
    monkeypatch.setattr("sys.argv", ["vox"])
    monkeypatch.setattr(cli, "_lock_dir", lambda: parent / "vox")
    try:
        with patch("vox.config.load_config") as load_config, pytest.raises(SystemExit) as exit_info:
            cli.main()
    finally:
        parent.chmod(0o700)
    assert exit_info.value.code == cli.EXIT_CANNOT_START == 78
    load_config.assert_not_called()


def test_version_flag_prints_the_package_version(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vox", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"vox {importlib.metadata.version('vox')}"


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
