from unittest.mock import patch

import pytest

from vox.config import Config, load_config, update_flag
from vox.errors import ConfigError
from vox.ui.settings_model import SettingsModel


def test_overlay_default_and_round_trips(tmp_path):
    path = tmp_path / 'config.toml'
    assert not Config().overlay_enabled
    assert not load_config(path).overlay_enabled
    original = '# preserved\n[audio]\nsample_rate = 16000 # custom\n[future]\nvalue = "keep"\n'
    path.write_text(original)
    for enabled in (True, False):
        update_flag(path, 'overlay', 'enabled', enabled)
        assert load_config(path).overlay_enabled is enabled
        assert original in path.read_text()


@pytest.mark.parametrize('value', ['1', '0', '"true"', '[]', '{}'])
def test_overlay_requires_boolean(tmp_path, value):
    path = tmp_path / 'config.toml'
    path.write_text(f'[overlay]\nenabled = {value}\n')
    with pytest.raises(ConfigError, match='overlay'):
        load_config(path)


def test_overlay_settings_save_failure_restores_file(tmp_path):
    path = tmp_path / 'config.toml'
    model = SettingsModel(path, devices=lambda channels: [])
    model.reload()
    assert model.set_overlay(True) is None
    assert model.config.overlay_enabled
    with patch('vox.config.os.replace', side_effect=OSError('read only')):
        assert model.set_overlay(False) == 'read only'
    assert model.config.overlay_enabled
    path.write_text('[overlay]\nenabled = "bad"')
    model.reload()
    assert model.set_overlay(False) == model.unreadable
