"""TOML configuration loading with defaults and env var fallback."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "vox" / "config.toml"


@dataclass
class Config:
    # API keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Hotkey
    hotkey: str = "right_shift"
    hotkey_fallback: str = "ctrl+space"

    # Audio
    audio_device: int | None = None
    sample_rate: int = 16000
    channels: int = 1
    max_recording_seconds: int = 30

    # Whisper
    whisper_model: str = "gpt-4o-mini-transcribe-2025-12-15"
    whisper_language: str | None = None
    whisper_prompt: str = "Use proper capitalization and punctuation. Remove filler words (um, uh, like, you know)."

    # Formatter
    formatter_model: str = "claude-haiku-4-5-20251001"
    skip_formatting: bool = True
    skip_formatting_max_words: int = 3

    # Snippets: trigger phrase -> expansion
    snippets: dict[str, str] = field(default_factory=dict)

    # Custom dictionary words to preserve
    dictionary: list[str] = field(default_factory=list)

    # Per-app style overrides (app_type -> style instruction)
    styles: dict[str, str] = field(default_factory=dict)

    # Window class overrides (wm_class -> app_type)
    window_classes: dict[str, str] = field(default_factory=dict)

    # Sounds
    sounds_enabled: bool = True

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def __post_init__(self) -> None:
        self._config_path: Path | None = None


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML file with env var fallback for API keys."""
    config = Config()

    config_path = path or DEFAULT_CONFIG_PATH
    config._config_path = config_path

    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            raise ConfigError(f"Failed to parse config file {config_path}: {e}") from e

        _apply_section(config, data, "api", {
            "openai_api_key": "openai_api_key",
            "anthropic_api_key": "anthropic_api_key",
        })
        _apply_section(config, data, "hotkey", {
            "key": "hotkey",
            "fallback": "hotkey_fallback",
        })
        _apply_section(config, data, "audio", {
            "device": "audio_device",
            "sample_rate": "sample_rate",
            "channels": "channels",
            "max_recording_seconds": "max_recording_seconds",
        })
        _apply_section(config, data, "whisper", {
            "model": "whisper_model",
            "language": "whisper_language",
            "prompt": "whisper_prompt",
        })
        _apply_section(config, data, "formatter", {
            "model": "formatter_model",
            "skip_formatting": "skip_formatting",
            "skip_formatting_max_words": "skip_formatting_max_words",
        })

        if "snippets" in data:
            config.snippets = dict(data["snippets"])
        if "dictionary" in data:
            config.dictionary = list(data["dictionary"])
        if "styles" in data:
            config.styles = dict(data["styles"])
        if "window_classes" in data:
            config.window_classes = dict(data["window_classes"])

        if "sounds" in data:
            if "enabled" in data["sounds"]:
                config.sounds_enabled = data["sounds"]["enabled"]

    # Env var fallback for API keys
    if not config.openai_api_key:
        config.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    if not config.anthropic_api_key:
        config.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    return config


def _apply_section(config: Config, data: dict, section: str, mapping: dict[str, str]) -> None:
    """Apply a TOML section's values to config fields."""
    if section not in data:
        return
    for toml_key, attr_name in mapping.items():
        if toml_key in data[section]:
            setattr(config, attr_name, data[section][toml_key])
