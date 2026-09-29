"""The Settings window's General and Transcription settings, without a toolkit."""

import os
import stat
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from vox import keystore
from vox.config import RECORDING_LIMIT_CHOICES, load_config
from vox.ui.settings_model import (
    DETECT_LANGUAGE,
    LANGUAGES,
    NOT_CONNECTED,
    SYSTEM_DEFAULT,
    SettingsModel,
    level_for,
    limit_label,
    percent,
    shown_path,
)

KEY = "sk-test-dummy-0001"
TEXT = "# my settings\n[audio]\nsample_rate = 48000  # keep\n"


def loaded(path, devices=(), key=""):
    model = SettingsModel(path, devices=lambda channels: list(devices), read_key=lambda: key)
    model.reload()
    model.reload_key()
    model.refresh_devices()
    return model


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(TEXT)
    return path


# -- Each setting ------------------------------------------------------------------


@pytest.mark.parametrize("change, section, key, value", [
    (lambda m: m.set_device("USB Mic"), "audio", "device", "USB Mic"),
    (lambda m: m.set_limit(1800), "audio", "max_recording_seconds", 1800),
    (lambda m: m.set_sounds(False), "sounds", "enabled", False),
    (lambda m: m.set_attenuation(False), "attenuation", "enabled", False),
    (lambda m: m.set_attenuation_percent(30), "attenuation", "level", 0.3),
    (lambda m: m.set_screen_hints(False), "context", "screen", False),
    (lambda m: m.set_mode("streaming"), "transcription", "mode", "streaming"),
    (lambda m: m.set_language("de"), "transcription", "language", "de"),
    (lambda m: m.set_prompt("Vox, pytest"), "transcription", "prompt", "Vox, pytest"),
    (lambda m: m.set_whisper_binary("/opt/bin/whisper-cli"), "whisper_cpp", "binary", "/opt/bin/whisper-cli"),
    (lambda m: m.set_whisper_model(str(Path.home() / "models" / "ggml.bin")), "whisper_cpp", "model", "~/models/ggml.bin"),
])
def test_each_setting_is_saved_at_once_and_read_back(cfg, change, section, key, value):
    model = loaded(cfg)
    assert change(model) is None
    assert tomllib.loads(cfg.read_text())[section][key] == value
    assert "sample_rate = 48000  # keep" in cfg.read_text()
    assert model.config == load_config(cfg)


def test_clearing_values_removes_them(cfg):
    cfg.write_text('[audio]\ndevice = "USB"\n[transcription]\nlanguage = "de"\nprompt = "Hi"\n[whisper_cpp]\nbinary = "/x"\n')
    model = loaded(cfg)
    for change in (lambda: model.set_device(None), lambda: model.set_language(None), lambda: model.set_prompt(""),
                   lambda: model.set_whisper_binary(None)):
        assert change() is None
    config = load_config(cfg)
    assert (config.audio_device, config.whisper_language, config.whisper_prompt) == (None, None, "")
    assert config.whisper_cpp_binary == "whisper-cli"


def test_a_change_that_cannot_be_saved_returns_the_reason_and_keeps_the_file(cfg):
    model = loaded(cfg)
    with patch("vox.config.os.replace", side_effect=OSError("Read-only file system")):
        assert model.set_sounds(False) == "Read-only file system"
    assert model.config.sounds_enabled is True  # the control goes back to the file's value
    assert cfg.read_text() == TEXT


def test_a_file_that_does_not_load_refuses_every_change(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[attenuation]\nlevel = "loud"\n')
    model = loaded(path)
    assert not model.writable
    assert model.unreadable.startswith("Couldn’t read ")
    for change in (lambda: model.set_sounds(False), lambda: model.set_mode("streaming"),
                   lambda: model.set_device("USB"), lambda: model.set_prompt("x")):
        assert change() == model.unreadable
    assert path.read_text() == '[attenuation]\nlevel = "loud"\n'


def test_opening_and_closing_without_a_change_creates_no_file(tmp_path):
    path = tmp_path / "config.toml"
    model = loaded(path, devices=[(0, "Built-in Mic")])
    model.choose_microphone(0)
    model.choose_language(0)
    model.set_limit(model.config.max_recording_seconds)
    model.set_sounds(True)
    model.set_mode(model.config.mode)
    model.set_prompt("")
    assert not path.exists()


def test_a_new_file_is_private(tmp_path):
    path = tmp_path / "vox" / "config.toml"
    loaded(path).set_sounds(False)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_poll_notices_a_hand_edit(cfg):
    model = loaded(cfg)
    assert model.poll() is False
    cfg.write_text(TEXT + "[sounds]\nenabled = false\n")
    os.utime(cfg, (1_000_000, 1_000_000))
    assert model.poll() is True
    assert model.config.sounds_enabled is False
    cfg.write_text("[sounds\n")
    assert model.poll() is True
    assert model.load_error and model.config.sounds_enabled is False  # the last good values stay
    cfg.write_text(TEXT)
    assert model.poll() is True
    assert model.writable and model.config.sounds_enabled is True
    assert model.set_limit(600) is None  # a later change keeps the hand edit
    assert "# my settings" in cfg.read_text()


# -- Microphones -----------------------------------------------------------------------

DEVICES = [(0, "MacBook Pro Microphone"), (2, "USB Audio Interface")]


def labels(choices):
    return [choice.label for choice in choices]


def test_system_default_comes_first_and_is_selected_without_a_device(cfg):
    choices, selected = loaded(cfg, DEVICES).microphones()
    assert labels(choices) == [SYSTEM_DEFAULT, "MacBook Pro Microphone", "USB Audio Interface"]
    assert selected == 0


def test_picking_a_microphone_saves_its_name_and_system_default_removes_it(cfg):
    model = loaded(cfg, DEVICES)
    assert model.choose_microphone(2) is None
    assert load_config(cfg).audio_device == "USB Audio Interface"
    assert model.microphones()[1] == 2
    assert model.choose_microphone(0) is None
    assert "device" not in tomllib.loads(cfg.read_text())["audio"]


def test_a_saved_device_that_is_not_connected_is_listed_and_selected(cfg):
    cfg.write_text('[audio]\ndevice = "AirPods"\n')
    model = loaded(cfg, DEVICES)
    choices, selected = model.microphones()
    assert labels(choices)[-1] == f"AirPods {NOT_CONNECTED}" and selected == 3
    before = cfg.read_bytes()
    assert model.choose_microphone(3) is None
    assert cfg.read_bytes() == before


def test_a_device_given_by_index_is_selected_and_replaced_by_a_name(cfg):
    cfg.write_text("[audio]\ndevice = 2\n")
    model = loaded(cfg, DEVICES)
    assert model.microphones()[1] == 2
    model.choose_microphone(1)
    assert load_config(cfg).audio_device == "MacBook Pro Microphone"

    cfg.write_text("[audio]\ndevice = 7\n")
    model = loaded(cfg, DEVICES)
    choices, selected = model.microphones()
    assert labels(choices)[selected] == f"Device 7 {NOT_CONNECTED}"
    assert model.choose_microphone(selected) is None
    assert load_config(cfg).audio_device == 7


def test_the_selection_follows_the_name_when_devices_are_renumbered(cfg):
    model = loaded(cfg, DEVICES)
    model.choose_microphone(2)
    model.devices = [(0, "AirPods"), (1, "MacBook Pro Microphone"), (5, "USB Audio Interface")]
    choices, selected = model.microphones()
    assert labels(choices)[selected] == "USB Audio Interface"


def test_an_exact_name_wins_and_of_two_same_names_the_first_is_selected(cfg):
    cfg.write_text('[audio]\ndevice = "USB Audio"\n')
    model = loaded(cfg, [(1, "USB Audio 2"), (4, "USB Audio")])
    assert model.microphones()[1] == 2
    model = loaded(cfg, [(1, "USB Audio"), (3, "USB Audio")])
    assert model.microphones()[1] == 1
    model.choose_microphone(2)  # the second one saves the same name
    assert model.microphones()[1] == 1


@pytest.mark.parametrize("spec", ["default", "Default ", "pulse", "usb audio", "USB Audio", "USB Audio 2", "alc257"])
def test_the_selected_device_is_the_one_the_recorder_opens(tmp_path, spec):
    from vox.audio import resolve_input_device
    from vox.config import update_audio_device

    names = ["HDA Intel PCH: ALC257 Analog (hw:0,0)", "sysdefault", "pulse", "default", "USB Audio 2", "USB Audio"]
    path = tmp_path / "config.toml"
    update_audio_device(path, spec)
    with patch("vox.audio.sd.query_devices", return_value=[{"name": n, "max_input_channels": 2} for n in names]):
        recorded = resolve_input_device(spec)
    choices, selected = loaded(path, list(enumerate(names))).microphones()
    assert labels(choices)[selected] == names[recorded]


def test_a_device_list_that_fails_leaves_system_default(cfg):
    def broken(channels):
        raise RuntimeError("PortAudio not initialized")

    model = SettingsModel(cfg, devices=broken)
    model.reload()
    model.refresh_devices()
    assert labels(model.microphones()[0]) == [SYSTEM_DEFAULT]


# -- Recording limit, volume and paths ---------------------------------------------------


def test_limits_list_the_choices_and_a_custom_value(cfg):
    choices, selected = loaded(cfg).limits()
    assert [c.value for c in choices] == list(RECORDING_LIMIT_CHOICES)
    assert choices[selected].label == "15 min"
    cfg.write_text("[audio]\nmax_recording_seconds = 1234\n")
    choices, selected = loaded(cfg).limits()
    assert choices[selected].value == 1234 and choices[selected].label == "1234 sec"
    assert limit_label(3600) == "60 min"


def test_attenuation_percent_uses_five_percent_steps_and_shows_off_step_values():
    assert percent(0.33) == 33 and percent(0.5) == 50
    assert level_for(30) == 0.3 and level_for(33) == 0.35 and level_for(31.9) == 0.3
    assert level_for(0) == 0.0 and level_for(100) == 1.0 and level_for(120) == 1.0


def test_an_off_step_level_is_kept_until_the_slider_moves(cfg):
    cfg.write_text("[attenuation]\nlevel = 0.33\n")
    model = loaded(cfg)
    assert percent(model.config.attenuation_level) == 33
    model.set_sounds(False)
    assert load_config(cfg).attenuation_level == 0.33


def test_paths_are_absolute_with_home_as_tilde(tmp_path):
    assert shown_path(Path.home() / "models" / "a.bin") == "~/models/a.bin"
    assert shown_path("~/x") == "~/x"
    assert shown_path("/opt/whisper-cli") == "/opt/whisper-cli"


# -- Language ------------------------------------------------------------------------


def test_languages_start_with_detect_and_are_sorted_by_name():
    assert LANGUAGES[0] == (None, DETECT_LANGUAGE)
    names = [name for _, name in LANGUAGES[1:]]
    assert names == sorted(names)
    assert ("de", "German") in LANGUAGES and ("yue", "Cantonese") in LANGUAGES


def test_picking_a_language_saves_its_code_and_detect_removes_it(cfg):
    model = loaded(cfg)
    choices, selected = model.languages()
    assert selected == 0
    german = next(row for row, c in enumerate(choices) if c.value == "de")
    assert model.choose_language(german) is None
    assert load_config(cfg).whisper_language == "de"
    assert model.languages()[1] == german
    assert model.choose_language(0) is None
    assert load_config(cfg).whisper_language is None


def test_a_language_code_that_is_not_listed_is_shown_and_kept(cfg):
    cfg.write_text('[transcription]\nlanguage = "xx"\n')
    model = loaded(cfg)
    choices, selected = model.languages()
    assert choices[selected].label == "xx"
    model.set_sounds(False)
    assert load_config(cfg).whisper_language == "xx"


# -- Mode availability ----------------------------------------------------------------


def local_setup(tmp_path):
    binary = tmp_path / "whisper-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    model = tmp_path / "ggml.bin"
    model.write_bytes(b"model")
    return binary, model


def availability(model):
    return {m.mode: (m.enabled, m.problem) for m in model.modes()}


def test_without_a_key_the_openai_modes_say_why_but_the_saved_one_stays_selectable(cfg):
    modes = availability(loaded(cfg))
    assert modes["batch"][0] is True and modes["batch"][1]  # saved: selectable, with its reason
    assert modes["streaming"] == (False, "Set an OpenAI API key before selecting OpenAI transcription")
    assert modes["whisper_cpp"][0] is False


def test_a_key_saved_on_the_page_enables_the_openai_modes(cfg, memory_keyring):
    model = SettingsModel(cfg, devices=lambda channels: [])
    model.reload()
    model.reload_key()
    assert availability(model)["streaming"][0] is False
    keystore.set_api_key(KEY)
    model.reload_key()
    assert availability(model)["streaming"] == (True, None)


def test_a_broken_local_setup_is_explained_and_fixing_it_on_the_page_enables_it(cfg, tmp_path):
    binary, ggml = local_setup(tmp_path)
    model = loaded(cfg, key=KEY)
    model.set_whisper_binary(str(binary))
    model.set_whisper_model(str(tmp_path / "missing.bin"))
    enabled, problem = availability(model)["whisper_cpp"]
    assert not enabled and "model not found" in problem
    assert model.set_whisper_model(str(ggml)) is None
    assert availability(model)["whisper_cpp"] == (True, None)
    assert model.set_mode("whisper_cpp") is None
    assert load_config(cfg).mode == "whisper_cpp"


def test_a_saved_local_mode_that_cannot_run_stays_selected_with_its_reason(cfg, tmp_path):
    binary, _ = local_setup(tmp_path)
    cfg.write_text(f'[transcription]\nmode = "whisper_cpp"\n[whisper_cpp]\nbinary = "{binary}"\n'
                   'model = "/nonexistent/ggml.bin"\n')
    modes = availability(loaded(cfg, key=KEY))
    assert modes["whisper_cpp"][0] is True and "model not found" in modes["whisper_cpp"][1]
    assert modes["batch"] == (True, None)


def test_an_unreadable_keychain_counts_as_no_key(cfg):
    def refuse():
        raise keystore.KeystoreError("locked")

    model = SettingsModel(cfg, read_key=refuse)
    model.reload()
    model.reload_key()
    assert model.api_key == ""
