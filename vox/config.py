"""TOML configuration loading with defaults and env var fallback."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "vox" / "config.toml"


@dataclass
class Config:
    # API keys
    openai_api_key: str = ""
    # Hotkey
    hotkey: str = "right_shift"
    hotkey_fallback: str = ""
    double_tap_timeout_ms: int = 400

    # Audio
    audio_device: int | str | None = None
    sample_rate: int = 48000
    channels: int = 1
    max_recording_seconds: int = 300

    # Transcription
    mode: str = "batch"
    streaming_model: str = "gpt-live-transcribe"
    whisper_cpp_binary: str = "whisper-cli"
    whisper_cpp_model: str = ""

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
    def transcription_mode(self) -> str:
        return self.mode

    @transcription_mode.setter
    def transcription_mode(self, value: str) -> None:
        self.mode = value

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
            "double_tap_timeout_ms": "double_tap_timeout_ms",
        })
        _apply_section(config, data, "audio", {
            "device": "audio_device",
            "sample_rate": "sample_rate",
            "channels": "channels",
            "max_recording_seconds": "max_recording_seconds",
        })
        _apply_section(config, data, "whisper", {
            "mode": "mode",
            "model": "whisper_model",
            "streaming_model": "streaming_model",
            "language": "whisper_language",
            "prompt": "whisper_prompt",
        })
        _apply_section(config, data, "whisper_cpp", {
            "binary": "whisper_cpp_binary",
            "model": "whisper_cpp_model",
        })
        if "transcription" in data:
            t = data["transcription"]
            if "mode" in t:
                config.mode = t["mode"]
            if "streaming_model" in t:
                config.streaming_model = t["streaming_model"]
            if "model" in t:
                if config.mode == "batch":
                    config.whisper_model = t["model"]
                elif config.mode == "streaming":
                    config.streaming_model = t["model"]
                else:
                    config.whisper_cpp_model = t["model"]
            if "language" in t:
                config.whisper_language = t["language"]
            if "prompt" in t:
                config.whisper_prompt = t["prompt"]

        if config.mode not in ("streaming", "batch", "whisper_cpp"):
            raise ConfigError(f"Invalid transcription mode '{config.mode}': must be 'streaming', 'batch', or 'whisper_cpp'")

        if "snippets" in data:
            config.snippets = dict(data["snippets"])
        dict_val = data.get("dictionary")
        if dict_val is None and "transcription" in data and "dictionary" in data["transcription"]:
            dict_val = data["transcription"]["dictionary"]
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


# Sections load_config() searches for `dictionary`, in precedence order (None = top level)
_DICTIONARY_SECTIONS: tuple[str | None, ...] = (None, "transcription", "attenuation", "whisper")


def update_dictionary(path: Path, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> list[str]:
    """Add and remove custom dictionary words in config.toml, keeping comments and layout.

    Edits `dictionary` wherever load_config() reads it from, creating a top-level
    key if none exists. Words already present (case-insensitively) are not added
    twice. Returns the resulting list.
    """
    doc = _read_document(path)
    container = doc
    for section in _DICTIONARY_SECTIONS:
        candidate = doc if section is None else doc.get(section)
        if candidate is not None and candidate.get("dictionary") is not None:
            container = candidate
            break
    if container.get("dictionary") is None:
        container["dictionary"] = tomlkit.array()
    words = container["dictionary"]

    removals = set(remove)
    for i in reversed(range(len(words))):
        if words[i] in removals:
            del words[i]
    seen = {str(w).lower() for w in words}
    for word in add:
        word = word.strip()
        if word and word.lower() not in seen:
            words.append(word)
            seen.add(word.lower())

    result = [str(w) for w in words]
    _write_document(path, doc)
    return result


def update_snippet(path: Path, trigger: str, expansion: str | None) -> dict[str, str]:
    """Set a snippet expansion in config.toml, or remove it when `expansion` is None.

    A trigger that matches an existing one the way the daemon compares them
    (ignoring case and trailing punctuation) replaces it. Returns the resulting
    snippets.
    """
    trigger = trigger.strip()
    if not trigger:
        raise ValueError("Snippet trigger must not be empty")
    if expansion is not None and not expansion.strip():
        raise ValueError("Snippet expansion must not be empty")

    doc = _read_document(path)
    if "snippets" not in doc:
        doc["snippets"] = tomlkit.table()
    snippets = doc["snippets"]
    for key in [k for k in snippets if snippet_key(k) == snippet_key(trigger)]:
        del snippets[key]
    if expansion is not None:
        snippets[trigger] = expansion

    result = {str(k): str(v) for k, v in snippets.items()}
    _write_document(path, doc)
    return result


def update_transcription_mode(path: Path, mode: str) -> None:
    """Persist the selected mode, keeping a legacy generic model with its provider."""
    if mode not in ("batch", "streaming", "whisper_cpp"):
        raise ValueError(f"Invalid transcription mode: {mode}")
    doc = _read_document(path)
    if "transcription" not in doc:
        doc["transcription"] = tomlkit.table()
    transcription = doc["transcription"]
    old_mode = transcription.get("mode", doc.get("whisper", {}).get("mode", "batch"))
    if old_mode != mode and "model" in transcription:
        old_model = transcription.pop("model")
        if old_mode == "streaming":
            if "streaming_model" not in transcription:
                transcription["streaming_model"] = old_model
        elif old_mode in ("batch", "whisper_cpp"):
            section = "whisper" if old_mode == "batch" else "whisper_cpp"
            if section not in doc:
                doc[section] = tomlkit.table()
            if "model" not in doc[section]:
                doc[section]["model"] = old_model
    transcription["mode"] = mode
    _write_document(path, doc)


def snippet_key(trigger: str) -> str:
    """Normalize a trigger the way the daemon matches it: ignoring case and trailing punctuation."""
    return trigger.strip().rstrip(".?!,").lower()


def _read_document(path: Path) -> tomlkit.TOMLDocument:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return tomlkit.document()
    try:
        return tomlkit.parse(text)
    except Exception as e:
        raise ConfigError(f"Failed to parse config file {path}: {e}") from e


def _write_document(path: Path, doc: tomlkit.TOMLDocument) -> None:
    """Write atomically (temp file + os.replace), following symlinks and keeping file permissions."""
    text = tomlkit.dumps(doc)
    tomllib.loads(text)  # never replace a valid config with an unparseable one

    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        with contextlib.suppress(FileNotFoundError):
            os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))  # new files keep the 0600 from mkstemp
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
