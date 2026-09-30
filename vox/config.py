"""TOML configuration loading with defaults. The OpenAI API key is not in here: see ``vox.keystore``."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import tomlkit

from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "vox" / "config.toml"


@dataclass
class Config:
    # Filled from vox.keystore at startup, never from config.toml
    openai_api_key: str = ""
    # Hotkey
    hotkey: str = "right_shift"
    hotkey_fallback: str = ""
    double_tap_timeout_ms: int = 400

    # Audio
    audio_device: int | str | None = None
    sample_rate: int = 48000
    channels: int = 1
    max_recording_seconds: int = 900

    # Transcription
    keep_failed_audio: bool = True
    mode: str = "batch"
    streaming_model: str = "gpt-live-transcribe"
    whisper_cpp_binary: str = "whisper-cli"
    whisper_cpp_model: str = ""
    # Run whisper.cpp again on the CPU when the graphics card has no memory left for the model
    whisper_cpp_cpu_fallback: bool = True

    # Whisper
    whisper_model: str = "gpt-transcribe"
    whisper_language: str | None = None
    whisper_prompt: str = ""

    # Snippets: trigger phrase -> expansion
    snippets: dict[str, str] = field(default_factory=dict)

    # Custom dictionary words to preserve
    dictionary: list[str] = field(default_factory=list)

    # Window class overrides (wm_class -> app_type)
    window_classes: dict[str, str] = field(default_factory=dict)

    # Screen context: send window-title and focused-window words to the transcriber as vocabulary hints
    context_screen: bool = True

    # Sounds
    sounds_enabled: bool = True

    # Attenuation
    attenuation_enabled: bool = True
    attenuation_level: float = 0.5

    @property
    def uses_openai(self) -> bool:
        """Whether the transcription mode sends audio to OpenAI, and so needs an API key."""
        return self.mode in ("batch", "streaming")

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def __post_init__(self) -> None:
        self._config_path: Path | None = None
        # Why the keychain couldn't be read, if it couldn't; a key may exist there, so don't ask for a new one
        self.api_key_error: str | None = None
        # Why the configured transcription mode can't run (e.g. a missing whisper.cpp model), set at startup
        self.mode_error: str | None = None
        # Why config.toml could not be loaded, if so: Vox then runs on defaults but does not record
        self.config_error: str | None = None


MODES: tuple[str, ...] = ("batch", "streaming", "whisper_cpp")

# Sections load_config() searches for `dictionary`, in precedence order (None = top level)
_DICTIONARY_SECTIONS: tuple[str | None, ...] = (None, "transcription", "attenuation", "whisper")


def load_config(path: Path | None = None) -> Config:
    """Load config from a TOML file, with defaults for anything it leaves out.

    Raises ConfigError naming the setting when a value has the wrong type or is out of range.
    """
    config = Config()

    config_path = path or DEFAULT_CONFIG_PATH
    config._config_path = config_path

    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            raise ConfigError(f"Failed to parse config file {config_path}: {e}") from e
        try:
            _apply(config, data)
        except ConfigError as e:
            raise ConfigError(f"{config_path}: {e}") from None

    return config


def file_stamp(path: Path) -> tuple[int, int, int] | None:
    """Changes whenever the file does, also when an older copy is moved back over it; None if it is missing."""
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size, st.st_ino


def fallback_config(path: Path | None, error: ConfigError) -> Config:
    """Default settings for a config.toml that failed to load, remembering why: Vox runs on them but won't record."""
    config = Config()
    config._config_path = path or DEFAULT_CONFIG_PATH
    config.config_error = str(error)
    return config


def _apply(config: Config, data: dict) -> None:
    _apply_section(config, data, "hotkey", {
        "key": ("hotkey", _text),
        "fallback": ("hotkey_fallback", _text),
        "double_tap_timeout_ms": ("double_tap_timeout_ms", _positive_int),
    })
    _apply_section(config, data, "audio", {
        "device": ("audio_device", _device),
        "sample_rate": ("sample_rate", _positive_int),
        "channels": ("channels", _positive_int),
        "max_recording_seconds": ("max_recording_seconds", _positive_int),
    })
    _apply_section(config, data, "whisper", {
        "mode": ("mode", _text),
        "model": ("whisper_model", _text),
        "streaming_model": ("streaming_model", _text),
        "language": ("whisper_language", _text),
        "prompt": ("whisper_prompt", _text),
    })
    _apply_section(config, data, "whisper_cpp", {
        "binary": ("whisper_cpp_binary", _text),
        "model": ("whisper_cpp_model", _text),
        "cpu_fallback": ("whisper_cpp_cpu_fallback", _flag),
    })
    _apply_section(config, data, "transcription", {
        "mode": ("mode", _text),
        "streaming_model": ("streaming_model", _text),
        "language": ("whisper_language", _text),
        "prompt": ("whisper_prompt", _text),
    })
    if "keep_failed_audio" in _section(data, "transcription"):
        config.keep_failed_audio = _flag("[transcription] keep_failed_audio", data["transcription"]["keep_failed_audio"])
    transcription = _section(data, "transcription")
    if "model" in transcription:
        # A generic model belongs to the provider the mode selects, overriding its own model setting
        model = _text("[transcription] model", transcription["model"])
        if config.mode == "batch":
            config.whisper_model = model
        elif config.mode == "streaming":
            config.streaming_model = model
        else:
            config.whisper_cpp_model = model

    if config.mode not in MODES:
        raise ConfigError(f"Invalid transcription mode '{config.mode}': must be 'streaming', 'batch', or 'whisper_cpp'")

    for section in _DICTIONARY_SECTIONS:
        container = data if section is None else _section(data, section)
        if container.get("dictionary") is not None:
            name = "dictionary" if section is None else f"[{section}] dictionary"
            config.dictionary = _words(name, container["dictionary"])
            break
    if "snippets" in data:
        config.snippets = _text_table("[snippets]", data["snippets"])
    if "window_classes" in data:
        config.window_classes = _text_table("[window_classes]", data["window_classes"])

    _apply_section(config, data, "context", {
        "screen": ("context_screen", _flag),
    })
    _apply_section(config, data, "sounds", {
        "enabled": ("sounds_enabled", _flag),
    })
    _apply_section(config, data, "attenuation", {
        "enabled": ("attenuation_enabled", _flag),
        "level": ("attenuation_level", _fraction),
    })


def _apply_section(config: Config, data: dict, section: str, mapping: dict[str, tuple[str, Callable]]) -> None:
    """Check a TOML section's values and apply them to config fields: {toml key: (field, check)}."""
    values = _section(data, section)
    for toml_key, (attr_name, check) in mapping.items():
        if toml_key in values:
            setattr(config, attr_name, check(f"[{section}] {toml_key}", values[toml_key]))


def _section(data: dict, section: str) -> dict:
    return _table(f"[{section}]", data.get(section, {}))


# -- Value checks: each returns the value, or raises ConfigError naming the setting --


def _invalid(name: str, expected: str, value: object) -> NoReturn:
    raise ConfigError(f"{name} must be {expected}, not {_describe(value)}")


def _describe(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, dict):
        return "a table"
    if isinstance(value, list):
        return "a list"
    return str(value)


def _text(name: str, value: object) -> str:
    if not isinstance(value, str):
        _invalid(name, "text in quotes", value)
    return value


def _flag(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        _invalid(name, "true or false", value)
    return value


def _positive_int(name: str, value: object) -> int:
    # bool is an int subclass, and TOML's true is not a number
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _invalid(name, "a whole number above 0", value)
    return value


def _fraction(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        _invalid(name, "a number from 0 to 1", value)
    return float(value)


def _device(name: str, value: object) -> int | str:
    if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
        return value
    _invalid(name, "a device index (0 or more) or a name in quotes", value)


def _words(name: str, value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(word, str) for word in value):
        _invalid(name, "a list of words in quotes", value)
    return list(value)


def _table(name: str, value: object) -> dict:
    if not isinstance(value, dict):
        _invalid(name, "a table", value)
    return value


def _text_table(name: str, value: object) -> dict[str, str]:
    table = _table(name, value)
    for key, item in table.items():
        _text(f'{name} "{key}"', item)
    return dict(table)


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
    if mode not in MODES:
        raise ValueError(f"Invalid transcription mode: {mode}")
    doc = _read_document(path)
    transcription = _edited_table(doc, "transcription")
    old_mode = transcription.get("mode", _edited_table(doc, "whisper", create=False).get("mode", "batch"))
    if old_mode != mode and "model" in transcription:
        # The generic model was the one in effect for the old provider, so it replaces that provider's own setting
        old_model = transcription.pop("model")
        if old_mode == "streaming":
            transcription["streaming_model"] = old_model
        elif old_mode in ("batch", "whisper_cpp"):
            _edited_table(doc, "whisper" if old_mode == "batch" else "whisper_cpp")["model"] = old_model
    transcription["mode"] = mode
    _write_document(path, doc)


RECORDING_LIMIT_CHOICES: tuple[int, ...] = (300, 600, 900, 1800, 3600)


def update_max_recording_seconds(path: Path, seconds: int) -> None:
    """Persist the recording limit in ``[audio] max_recording_seconds``, keeping comments and layout."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        raise ValueError(f"Invalid recording limit: {seconds!r}")
    doc = _read_document(path)
    _edited_table(doc, "audio")["max_recording_seconds"] = seconds
    _write_document(path, doc)


def update_hotkey(path: Path, key: str, fallback: str) -> None:
    """Persist ``[hotkey] key``, and ``fallback`` or its removal when empty, keeping comments and layout."""
    if not key.strip():
        raise ValueError("The hotkey can't be empty")
    doc = _read_document(path)
    hotkey = _edited_table(doc, "hotkey")
    hotkey["key"] = key
    if fallback:
        hotkey["fallback"] = fallback
    elif "fallback" in hotkey:
        del hotkey["fallback"]
    _write_document(path, doc)


# The on/off settings the Settings window shows: (section, key)
FLAGS: tuple[tuple[str, str], ...] = (("sounds", "enabled"), ("attenuation", "enabled"), ("context", "screen"), ("transcription", "keep_failed_audio"))


def update_audio_device(path: Path, name: str | None) -> None:
    """Persist the input device by name in ``[audio] device``; None or "" removes it (the system default)."""
    doc = _read_document(path)
    if name:
        _edited_table(doc, "audio")["device"] = name
    else:
        _remove_key(doc, "audio", "device")
    _write_document(path, doc)


def update_flag(path: Path, section: str, key: str, value: bool) -> None:
    """Persist one of the on/off settings in ``FLAGS``, such as ``[sounds] enabled``."""
    if (section, key) not in FLAGS or not isinstance(value, bool):
        raise ValueError(f"Invalid setting: [{section}] {key} = {value!r}")
    doc = _read_document(path)
    _edited_table(doc, section)[key] = value
    _write_document(path, doc)


def update_attenuation_level(path: Path, level: float) -> None:
    """Persist how far the volume is lowered while recording, in ``[attenuation] level`` (0 to 1)."""
    if isinstance(level, bool) or not isinstance(level, int | float) or not 0 <= level <= 1:
        raise ValueError(f"Invalid attenuation level: {level!r}")
    doc = _read_document(path)
    _edited_table(doc, "attenuation")["level"] = float(level)
    _write_document(path, doc)


def update_language(path: Path, code: str | None) -> None:
    """Persist the spoken language in ``[transcription] language``; None or "" removes it (detect automatically)."""
    _update_transcription_text(path, "language", (code or "").strip())


def update_prompt(path: Path, text: str) -> None:
    """Persist ``[transcription] prompt``; an empty prompt removes it."""
    _update_transcription_text(path, "prompt", text if text.strip() else "")


def _update_transcription_text(path: Path, key: str, value: str) -> None:
    doc = _read_document(path)
    if value:
        _edited_table(doc, "transcription")[key] = value
    else:
        # A legacy [whisper] value would apply once [transcription] no longer overrides it
        _remove_key(doc, "transcription", key)
        _remove_key(doc, "whisper", key)
    _write_document(path, doc)


_UNCHANGED = object()


def update_whisper_cpp(path: Path, *, binary: object = _UNCHANGED, model: object = _UNCHANGED) -> None:
    """Persist ``[whisper_cpp] binary`` and ``model``; None or "" removes one, and one not given stays."""
    doc = _read_document(path)
    for key, value in (("binary", binary), ("model", model)):
        if value is _UNCHANGED:
            continue
        if value:
            _edited_table(doc, "whisper_cpp")[key] = value
        else:
            _remove_key(doc, "whisper_cpp", key)
    if model is not _UNCHANGED:
        # A generic [transcription] model is the local model while the mode is local, and would shadow this one
        transcription = _edited_table(doc, "transcription", create=False)
        mode = transcription.get("mode", _edited_table(doc, "whisper", create=False).get("mode", "batch"))
        if mode == "whisper_cpp" and "model" in transcription:
            del transcription["model"]
    _write_document(path, doc)


def read_api_key_setting(path: Path) -> str:
    """The key in a legacy ``[api] openai_api_key`` setting, or "". Vox no longer reads keys from here."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Failed to parse config file {path}: {e}") from e
    api = data.get("api")
    value = api.get("openai_api_key", "") if isinstance(api, dict) else ""
    return value.strip() if isinstance(value, str) else ""


def remove_api_key_setting(path: Path) -> None:
    """Delete ``[api] openai_api_key``, keeping the rest of the file; drop ``[api]`` only if nothing else is in it."""
    doc = _read_document(path)
    api = doc.get("api")
    if api is None or "openai_api_key" not in api:
        return
    del api["openai_api_key"]
    if not api.as_string().strip():
        del doc["api"]
    _replace_document(path, doc)


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


def _edited_table(doc: tomlkit.TOMLDocument, name: str, *, create: bool = True) -> dict:
    """The ``[name]`` table of a document being edited, added if missing (an empty dict if not ``create``).

    Raises ConfigError when the file has something else under that name, as load_config() does.
    """
    if name not in doc:
        if not create:
            return {}
        doc[name] = tomlkit.table()
    return _table(f"[{name}]", doc[name])


def _remove_key(doc: tomlkit.TOMLDocument, section: str, key: str) -> None:
    """Delete ``key`` from ``[section]`` if it is there, and the table too if nothing else is left in it."""
    table = _edited_table(doc, section, create=False)
    if key in table:
        del table[key]
        if not table.as_string().strip():
            del doc[section]


def _write_document(path: Path, doc: tomlkit.TOMLDocument) -> None:
    """Write config.toml atomically, keeping its permissions. Snippets can be personal, so a new file is 0600.

    Refuses, with ConfigError, a result that wouldn't load. Writes nothing when the result loads to the
    same settings the file has now, so setting a value it already has never creates or touches the file.
    """
    text = tomlkit.dumps(doc)
    settings = Config()
    try:
        _apply(settings, tomllib.loads(text))
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from None
    if settings == _current_settings(path):
        return
    write_atomically(path, text)


def _current_settings(path: Path) -> Config | None:
    """The settings config.toml loads to now (the defaults when it is missing), or None if it doesn't load."""
    try:
        return load_config(path)
    except ConfigError:
        return None


def _replace_document(path: Path, doc: tomlkit.TOMLDocument) -> None:
    text = tomlkit.dumps(doc)
    tomllib.loads(text)  # never replace a valid config with an unparseable one
    write_atomically(path, text)


def write_atomically(path: Path, text: str, *, keep_mode: bool = True) -> None:
    """Replace ``path``, or the file a symlink there points to, with ``text`` (temp file + os.replace).

    A new file is owner-only (0600), in an owner-only directory if that is new too. An existing file
    keeps its permissions with ``keep_mode``, and is made owner-only without it.
    """
    target = path.resolve()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if keep_mode:
            with contextlib.suppress(FileNotFoundError):
                os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
