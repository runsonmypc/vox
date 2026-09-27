"""Unit tests for configuration loading and validation."""

import os
import tempfile
from pathlib import Path

import pytest

from vox.config import Config, load_config
from vox.errors import ConfigError


def test_config_defaults():
    config = Config()
    assert config.mode == "batch"
    assert config.streaming_model == "gpt-live-transcribe"
    assert config.whisper_model == "gpt-transcribe"
    assert config.double_tap_timeout_ms == 400


def test_load_config_transcription_section():
    toml_content = """
[transcription]
mode = "streaming"
streaming_model = "gpt-live-transcribe-v2"
prompt = "test prompt"
language = "en"
dictionary = ["Vox", "OpenAI"]
"""
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        config = load_config(temp_path)
        assert config.mode == "streaming"
        assert config.streaming_model == "gpt-live-transcribe-v2"
        assert config.whisper_prompt == "test prompt"
        assert config.whisper_language == "en"
        assert config.dictionary == ["Vox", "OpenAI"]
    finally:
        temp_path.unlink(missing_ok=True)


def test_load_config_batch_mode_in_transcription():
    toml_content = """
[transcription]
mode = "batch"
model = "whisper-1"
"""
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        config = load_config(temp_path)
        assert config.mode == "batch"
        assert config.whisper_model == "whisper-1"
    finally:
        temp_path.unlink(missing_ok=True)


def test_load_config_whisper_cpp_mode(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('''[transcription]
mode = "whisper_cpp"
[whisper_cpp]
binary = "bin/whisper-cli"
model = "models/ggml-base.en.bin"
''')
    config = load_config(path)
    assert config.mode == "whisper_cpp"
    assert config.whisper_cpp_binary == "bin/whisper-cli"
    assert config.whisper_cpp_model == "models/ggml-base.en.bin"


def test_load_config_whisper_section_override():
    toml_content = """
[whisper]
mode = "batch"
model = "whisper-1"
streaming_model = "gpt-live-transcribe"
"""
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        config = load_config(temp_path)
        assert config.mode == "batch"
        assert config.whisper_model == "whisper-1"
        assert config.streaming_model == "gpt-live-transcribe"
    finally:
        temp_path.unlink(missing_ok=True)


def test_load_config_invalid_mode_raises():
    toml_content = """
[transcription]
mode = "invalid-mode"
"""
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        with pytest.raises(ConfigError, match="Invalid transcription mode"):
            load_config(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def test_load_config_hotkey_section():
    toml_content = """
[hotkey]
key = "caps_lock"
fallback = "ctrl+space"
double_tap_timeout_ms = 250
"""
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        config = load_config(temp_path)
        assert config.hotkey == "caps_lock"
        assert config.hotkey_fallback == "ctrl+space"
        assert config.double_tap_timeout_ms == 250
    finally:
        temp_path.unlink(missing_ok=True)


def test_uses_openai_only_for_the_openai_modes():
    assert Config(mode="batch").uses_openai
    assert Config(mode="streaming").uses_openai
    assert not Config(mode="whisper_cpp").uses_openai


def test_key_files_are_not_read_into_config_or_the_environment(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[api]\nopenai_api_key = "sk-test-from-toml"\n')
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-test-from-env-file\nVOX_TEST_OTHER=1\n")
    monkeypatch.delenv("VOX_TEST_OTHER", raising=False)
    config = load_config(path)
    assert config.openai_api_key == ""
    assert "OPENAI_API_KEY" not in os.environ
    assert "VOX_TEST_OTHER" not in os.environ


# -- Checking values ----------------------------------------------------------------


def load(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return load_config(path)


@pytest.mark.parametrize("text, message", [
    ("sounds = false\n", r"\[sounds\] must be a table, not false"),
    ('snippets = ["a"]\n', r"\[snippets\] must be a table, not a list"),
    ('[snippets]\n"my email" = 5\n', r'\[snippets\] "my email" must be text in quotes, not 5'),
    ('[window_classes]\nSlack = ["CHAT"]\n', r'\[window_classes\] "Slack" must be text'),
    ('dictionary = "Kubernetes"\n', r'dictionary must be a list of words in quotes, not "Kubernetes"'),
    ('[whisper]\ndictionary = ["Vox", 1]\n', r"\[whisper\] dictionary must be a list of words"),
    ('[attenuation]\nlevel = "0.5"\n', r'\[attenuation\] level must be a number from 0 to 1, not "0.5"'),
    ("[attenuation]\nlevel = 50\n", r"\[attenuation\] level must be a number from 0 to 1, not 50"),
    ("[attenuation]\nlevel = -0.1\n", r"\[attenuation\] level must be a number"),
    ("[attenuation]\nlevel = nan\n", r"\[attenuation\] level must be a number"),
    ("[attenuation]\nlevel = true\n", r"\[attenuation\] level must be a number from 0 to 1, not true"),
    ('[sounds]\nenabled = "false"\n', r'\[sounds\] enabled must be true or false, not "false"'),
    ("[attenuation]\nenabled = 1\n", r"\[attenuation\] enabled must be true or false, not 1"),
    ("[context]\nscreen = 0\n", r"\[context\] screen must be true or false, not 0"),
    ("[audio]\nmax_recording_seconds = 0\n", r"\[audio\] max_recording_seconds must be a whole number above 0, not 0"),
    ("[audio]\nmax_recording_seconds = -5\n", r"\[audio\] max_recording_seconds must be a whole number above 0"),
    ("[audio]\nmax_recording_seconds = 90.5\n", r"\[audio\] max_recording_seconds must be a whole number above 0, not 90.5"),
    ("[audio]\nmax_recording_seconds = true\n", r"\[audio\] max_recording_seconds must be a whole number above 0, not true"),
    ('[audio]\nsample_rate = "16000"\n', r"\[audio\] sample_rate must be a whole number above 0"),
    ("[audio]\nchannels = 0\n", r"\[audio\] channels must be a whole number above 0"),
    ("[audio]\ndevice = -1\n", r"\[audio\] device must be a device index \(0 or more\) or a name in quotes, not -1"),
    ("[audio]\ndevice = true\n", r"\[audio\] device must be"),
    ("[hotkey]\ndouble_tap_timeout_ms = 0\n", r"\[hotkey\] double_tap_timeout_ms must be a whole number above 0"),
    ("[hotkey]\nkey = 5\n", r"\[hotkey\] key must be text in quotes, not 5"),
    ("[transcription]\nmode = 1\n", r"\[transcription\] mode must be text in quotes, not 1"),
    ('[transcription]\nmodel = ["gpt-transcribe"]\n', r"\[transcription\] model must be text in quotes, not a list"),
    ("[whisper_cpp]\nmodel = 3\n", r"\[whisper_cpp\] model must be text"),
    ('transcription = "streaming"\n', r'\[transcription\] must be a table, not "streaming"'),
])
def test_wrong_types_and_ranges_raise_config_error_naming_the_setting(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message) as error:
        load(tmp_path, text)
    assert str(tmp_path / "config.toml") in str(error.value)


@pytest.mark.parametrize("level, expected", [("0", 0.0), ("1", 1.0), ("0.25", 0.25), ("1.0", 1.0)])
def test_attenuation_level_accepts_ints_and_floats_from_0_to_1(tmp_path, level, expected):
    config = load(tmp_path, f"[attenuation]\nlevel = {level}\n")
    assert config.attenuation_level == expected
    assert type(config.attenuation_level) is float


def test_valid_values_of_every_type_load(tmp_path):
    config = load(tmp_path, """\
dictionary = ["Vox"]
[hotkey]
double_tap_timeout_ms = 250
[audio]
device = 0
sample_rate = 16000
channels = 2
max_recording_seconds = 1800
[context]
screen = false
[sounds]
enabled = false
[attenuation]
enabled = false
level = 0.3
[snippets]
"sign off" = "Best,\\nAlex"
[window_classes]
Slack = "CHAT"
""")
    assert config.dictionary == ["Vox"]
    assert config.double_tap_timeout_ms == 250
    assert (config.audio_device, config.sample_rate, config.channels) == (0, 16000, 2)
    assert config.max_recording_seconds == 1800
    assert config.context_screen is False
    assert config.sounds_enabled is False
    assert (config.attenuation_enabled, config.attenuation_level) == (False, 0.3)
    assert config.snippets == {"sign off": "Best,\nAlex"}
    assert config.window_classes == {"Slack": "CHAT"}
    assert load(tmp_path, '[audio]\ndevice = "USB"\n').audio_device == "USB"


def test_context_screen_defaults_on_and_loads_from_the_context_section(tmp_path):
    assert load(tmp_path, "").context_screen is True
    assert load(tmp_path, "[context]\nscreen = false\n").context_screen is False
    assert load(tmp_path, "[context]\nscreen = true\n").context_screen is True


def test_the_old_styles_table_is_ignored(tmp_path):
    """Left over from a removed formatter: nothing reads it, so it is neither loaded nor rejected."""
    config = load(tmp_path, '[styles]\nCHAT = "casual"\n')
    assert not hasattr(config, "styles")


def test_dictionary_comes_from_the_first_section_that_has_one(tmp_path):
    text = '[whisper]\ndictionary = ["W"]\n[attenuation]\ndictionary = ["A"]\n'
    assert load(tmp_path, text).dictionary == ["A"]
    assert load(tmp_path, 'dictionary = ["Top"]\n' + text).dictionary == ["Top"]
    assert load(tmp_path, '[transcription]\ndictionary = ["T"]\n' + text).dictionary == ["T"]
