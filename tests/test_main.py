"""Tests for the vox command's startup checks."""

from unittest.mock import patch

from vox import __main__ as cli


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
