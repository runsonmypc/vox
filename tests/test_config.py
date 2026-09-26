"""Unit tests for configuration loading and validation."""

import tempfile
from pathlib import Path
import pytest

from vox.config import Config, load_config
from vox.errors import ConfigError


def test_config_defaults():
    config = Config()
    assert config.mode == "batch"
    assert config.transcription_mode == "batch"
    assert config.streaming_model == "gpt-live-transcribe"
    assert config.whisper_model == "gpt-4o-mini-transcribe-2025-12-15"
    assert config.double_tap_timeout_ms == 400


def test_transcription_mode_setter():
    config = Config()
    config.transcription_mode = "streaming"
    assert config.mode == "streaming"
    assert config.transcription_mode == "streaming"


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
