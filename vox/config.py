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
    # Hotkey
    hotkey: str = "right_shift"
    hotkey_fallback: str = ""

    # Audio
    audio_device: int | None = None
    sample_rate: int = 16000
    channels: int = 1
    max_recording_seconds: int = 300

    # Whisper
    whisper_model: str = "gpt-4o-mini-transcribe-2025-12-15"
    whisper_language: str | None = None
    whisper_prompt: str = ""

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

    # Attenuation
    attenuation_enabled: bool = True
    attenuation_level: float = 0.5

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def __post_init__(self) -> None:
        self._config_path: Path | None = None


_dotenv_loaded = False


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML file with env var fallback for API keys."""
    global _dotenv_loaded
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
        if "snippets" in data:
            config.snippets = dict(data["snippets"])
        dict_val = data.get("dictionary")
        if dict_val is None and "attenuation" in data and "dictionary" in data["attenuation"]:
            dict_val = data["attenuation"]["dictionary"]
        if dict_val is None and "whisper" in data and "dictionary" in data["whisper"]:
            dict_val = data["whisper"]["dictionary"]
        if dict_val is not None:
            config.dictionary = list(dict_val)
        if "styles" in data:
            config.styles = dict(data["styles"])
        if "window_classes" in data:
            config.window_classes = dict(data["window_classes"])

        _apply_section(config, data, "sounds", {
            "enabled": "sounds_enabled",
        })
        _apply_section(config, data, "attenuation", {
            "enabled": "attenuation_enabled",
            "level": "attenuation_level",
        })

    # Load .env file once from project root and config dir
    if not _dotenv_loaded:
        _load_dotenv(Path(__file__).resolve().parent.parent)  # repo root
        _load_dotenv(config_path.parent)
        _dotenv_loaded = True

    # Env var fallback for API keys
    if not config.openai_api_key:
        config.openai_api_key = os.environ.get("OPENAI_API_KEY", "")

    return config


def _load_dotenv(directory: Path) -> None:
    """Load KEY=VALUE pairs from .env file into os.environ (won't overwrite)."""
    env_file = directory / ".env"
    if not env_file.is_file():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value


def _apply_section(config: Config, data: dict, section: str, mapping: dict[str, str]) -> None:
    """Apply a TOML section's values to config fields."""
    if section not in data:
        return
    for toml_key, attr_name in mapping.items():
        if toml_key in data[section]:
            setattr(config, attr_name, data[section][toml_key])
