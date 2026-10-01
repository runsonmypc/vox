"""What the General and Transcription pages of Settings show and change, independent of toolkit.

Every change is written to config.toml at once, through the writers in ``vox.config``, and read
back, so the window always shows what the daemon will load. The Hotkey, Vocabulary, Snippets and
API key parts keep their own models (``HotkeyModel``, ``VocabModel``, ``KeyModel``).
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import keystore
from ..audio import match_input_device
from ..config import (
    MODES,
    RECORDING_LIMIT_CHOICES,
    Config,
    file_stamp,
    load_config,
    update_attenuation_level,
    update_audio_device,
    update_flag,
    update_language,
    update_max_recording_seconds,
    update_prompt,
    update_transcription_mode,
    update_whisper_cpp,
)
from ..errors import ConfigError
from ..modes import LABELS, mode_problem

log = logging.getLogger(__name__)

PAGES: tuple[str, ...] = ("general", "hotkey", "transcription", "vocabulary", "snippets")

# What both windows say, so macOS and Linux never drift apart
TITLE = "Settings"
SAVE_FAILED_TITLE = "Couldn’t Save"
SYSTEM_DEFAULT = "System Default"
NOT_CONNECTED = "(not connected)"
DETECT_LANGUAGE = "Detect automatically"
MICROPHONE = "Microphone"
RECORDING_LIMIT = "Recording limit"
RECORDING_LIMIT_NOTE = "A recording stops and is transcribed once it reaches this length."
KEEP_FAILED_AUDIO = "Keep failed recordings for retry"
KEEP_FAILED_AUDIO_NOTE = (
    "Save failed audio locally until recovery or deletion. Turning this off stops new saves and retries. "
    "Delete existing recordings in History."
)
SOUNDS = "Sounds"
SOUNDS_NOTE = "Play a sound when dictation starts, stops, is cancelled or fails."
LOWER_AUDIO = "Lower other audio while recording"
LOWER_AUDIO_LEVEL = "Volume while recording"
SCREEN_HINTS = "Screen hints"
SCREEN_HINTS_NOTE = "Send words from the window you dictate into, so names and terms come out right."
PRIVACY_LINK = "What screen hints send"
PRIVACY_URL = "https://github.com/runsonmypc/vox/blob/main/docs/privacy.md"
MODE = "Transcription"
LANGUAGE = "Spoken language"
PROMPT = "Prompt"
PROMPT_NOTE = "Words or a sentence in your style, sent with each recording to guide spelling and punctuation."
WHISPER_BINARY = "whisper.cpp program"
WHISPER_MODEL = "whisper.cpp model"
CHOOSE = "Choose…"
DICTATION_OFF = "Dictation is off while Settings is open."

# The languages OpenAI's transcription models name: Whisper's list, which OpenAI's documentation points
# to. Javanese is left out: Whisper calls it "jw", but the gpt-transcribe models take ISO 639-1 ("jv").
_LANGUAGES = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "as": "Assamese", "az": "Azerbaijani", "ba": "Bashkir",
    "be": "Belarusian", "bg": "Bulgarian", "bn": "Bengali", "bo": "Tibetan", "br": "Breton", "bs": "Bosnian",
    "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish", "fo": "Faroese",
    "fr": "French", "gl": "Galician", "gu": "Gujarati", "ha": "Hausa", "haw": "Hawaiian", "he": "Hebrew",
    "hi": "Hindi", "hr": "Croatian", "ht": "Haitian Creole", "hu": "Hungarian", "hy": "Armenian", "id": "Indonesian",
    "is": "Icelandic", "it": "Italian", "ja": "Japanese", "ka": "Georgian", "kk": "Kazakh", "km": "Khmer",
    "kn": "Kannada", "ko": "Korean", "la": "Latin", "lb": "Luxembourgish", "ln": "Lingala", "lo": "Lao",
    "lt": "Lithuanian", "lv": "Latvian", "mg": "Malagasy", "mi": "Maori", "mk": "Macedonian", "ml": "Malayalam",
    "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "mt": "Maltese", "my": "Burmese", "ne": "Nepali",
    "nl": "Dutch", "nn": "Norwegian Nynorsk", "no": "Norwegian", "oc": "Occitan", "pa": "Punjabi", "pl": "Polish",
    "ps": "Pashto", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sa": "Sanskrit", "sd": "Sindhi",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "sn": "Shona", "so": "Somali", "sq": "Albanian",
    "sr": "Serbian", "su": "Sundanese", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil", "te": "Telugu",
    "tg": "Tajik", "th": "Thai", "tk": "Turkmen", "tl": "Tagalog", "tr": "Turkish", "tt": "Tatar",
    "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese", "yi": "Yiddish", "yo": "Yoruba",
    "yue": "Cantonese", "zh": "Chinese",
}
# (code, name), by name, with "Detect automatically" (no code) first
LANGUAGES: tuple[tuple[str | None, str], ...] = (
    (None, DETECT_LANGUAGE), *sorted(_LANGUAGES.items(), key=lambda item: item[1]),
)

ATTENUATION_STEP = 5  # percent


@dataclass(frozen=True)
class Choice:
    """One row of a popup: what it shows, and the value choosing it saves."""

    label: str
    value: object


@dataclass(frozen=True)
class ModeChoice:
    mode: str
    label: str
    problem: str | None  # why it can't run now, shown under it
    enabled: bool  # the saved mode stays selectable, so the user sees why it can't run


def limit_label(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds % 60 == 0 else f"{seconds} sec"


def percent(level: float) -> int:
    """How a level from the file shows on the slider: 0.33 shows as 33."""
    return round(level * 100)


def level_for(value: float) -> float:
    """The level a slider position saves, on the 5% steps the slider moves in."""
    steps = round(value / ATTENUATION_STEP)
    return round(min(max(steps * ATTENUATION_STEP, 0), 100) / 100, 2)


def shown_path(path: str | Path) -> str:
    """A path as Settings shows and saves it: absolute, with the home folder written as ~."""
    absolute = Path(path).expanduser()
    home = Path.home()
    if absolute == home:
        return "~"
    if home in absolute.parents:
        return str(Path("~") / absolute.relative_to(home))
    return str(absolute)


def list_input_devices(channels: int = 1) -> list[tuple[int, str]]:
    """The input devices as (index, name), as the recorder sees them. Blocks while PortAudio starts."""
    import sounddevice as sd

    return [
        (i, d.get("name", f"Device {i}"))
        for i, d in enumerate(sd.query_devices())
        if d.get("max_input_channels", 0) >= channels
    ]


class SettingsModel:
    def __init__(
        self,
        path: Path,
        *,
        devices: Callable[[int], list[tuple[int, str]]] = list_input_devices,
        read_key: Callable[[], str] = keystore.get_api_key,
    ) -> None:
        self.path = path
        self.config = Config()
        self.load_error: str | None = None
        self.devices: list[tuple[int, str]] = []
        self.api_key = ""
        self._list_devices = devices
        self._read_key = read_key
        self._stamp: tuple[int, int, int] | None = None

    @property
    def shown_path(self) -> str:
        return shown_path(self.path)

    @property
    def unreadable(self) -> str:
        return f"Couldn’t read {self.shown_path}: {self.load_error}"

    @property
    def writable(self) -> bool:
        return self.load_error is None

    # -- Reading ----------------------------------------------------------

    def reload(self) -> None:
        """Re-read config.toml. On failure, keep the last good settings and set ``load_error``."""
        self._stamp = file_stamp(self.path)
        try:
            config = load_config(self.path)
        except ConfigError as e:
            self.load_error = str(e)
            return
        self.load_error = None
        self.config = config

    def poll(self) -> bool:
        """Re-read config.toml if it changed since it was last read; returns whether it did."""
        if file_stamp(self.path) == self._stamp:
            return False
        self.reload()
        return True

    def reload_key(self) -> None:
        """Re-read the API key, which decides whether the OpenAI modes can be chosen."""
        try:
            self.api_key = self._read_key()
        except keystore.KeystoreError as e:
            log.warning("Couldn't read the OpenAI API key: %s", e)
            self.api_key = ""

    def refresh_devices(self) -> None:
        """List the input devices again, for a microphone connected or removed since."""
        try:
            self.devices = self._list_devices(self.config.channels)
        except Exception as e:
            log.warning("Couldn't list the input devices: %s", e)
            self.devices = []

    # -- Changing ---------------------------------------------------------

    def _change(self, write: Callable[[], object]) -> str | None:
        """Write a change and read the file back. Returns why it couldn't be saved, or None."""
        if self.load_error is not None:
            return self.unreadable
        try:
            write()
        except (ConfigError, OSError, ValueError) as e:
            log.warning("Couldn't save a setting: %s", e)
            self.reload()  # so the control goes back to the value in the file
            return str(e)
        self.reload()
        return None

    def set_device(self, name: str | None) -> str | None:
        return self._change(lambda: update_audio_device(self.path, name))

    def set_limit(self, seconds: int) -> str | None:
        return self._change(lambda: update_max_recording_seconds(self.path, seconds))

    def set_keep_failed_audio(self, on: bool) -> str | None:
        return self._change(lambda: update_flag(self.path, "transcription", "keep_failed_audio", on))

    def set_sounds(self, on: bool) -> str | None:
        return self._change(lambda: update_flag(self.path, "sounds", "enabled", on))

    def set_overlay(self, on: bool) -> str | None:
        return self._change(lambda: update_flag(self.path, "overlay", "enabled", on))

    def set_attenuation(self, on: bool) -> str | None:
        return self._change(lambda: update_flag(self.path, "attenuation", "enabled", on))

    def set_attenuation_percent(self, value: float) -> str | None:
        return self._change(lambda: update_attenuation_level(self.path, level_for(value)))

    def set_screen_hints(self, on: bool) -> str | None:
        return self._change(lambda: update_flag(self.path, "context", "screen", on))

    def set_mode(self, mode: str) -> str | None:
        return self._change(lambda: update_transcription_mode(self.path, mode))

    def set_language(self, code: str | None) -> str | None:
        return self._change(lambda: update_language(self.path, code))

    def set_prompt(self, text: str) -> str | None:
        return self._change(lambda: update_prompt(self.path, text))

    def set_whisper_binary(self, path: str | None) -> str | None:
        return self._change(lambda: update_whisper_cpp(self.path, binary=shown_path(path) if path else None))

    def set_whisper_model(self, path: str | None) -> str | None:
        return self._change(lambda: update_whisper_cpp(self.path, model=shown_path(path) if path else None))

    # -- Microphone -------------------------------------------------------

    def microphones(self) -> tuple[list[Choice], int]:
        """The microphone popup's rows and the selected one. A saved device that isn't connected is
        listed as "(not connected)", selected, and choosing it saves nothing."""
        choices = [Choice(SYSTEM_DEFAULT, None)] + [Choice(name, name) for _, name in self.devices]
        spec = self.config.audio_device
        if spec is None:
            return choices, 0
        if isinstance(spec, int):
            index = spec if any(i == spec for i, _ in self.devices) else None
        else:
            index = match_input_device(spec, self.devices)
        if index is not None:
            # Only a name is saved, so of two devices with the same name the first records: select that one
            return choices, 1 + next(row for row, (i, _) in enumerate(self.devices) if i == index)
        missing = spec if isinstance(spec, str) else f"Device {spec}"
        choices.append(Choice(f"{missing} {NOT_CONNECTED}", spec))
        return choices, len(choices) - 1

    def choose_microphone(self, row: int) -> str | None:
        value = self.microphones()[0][row].value
        if value is not None and not isinstance(value, str):
            return None  # the missing device given by index: there is no name to save in its place
        return self.set_device(value)  # writes nothing when the name is the one saved

    # -- Recording limit --------------------------------------------------

    def limits(self) -> tuple[list[Choice], int]:
        """The limit popup's rows, with a custom limit from the file listed too, and the selected one."""
        current = self.config.max_recording_seconds
        values = sorted({*RECORDING_LIMIT_CHOICES, current})
        return [Choice(limit_label(s), s) for s in values], values.index(current)

    # -- Transcription ----------------------------------------------------

    def modes(self) -> list[ModeChoice]:
        """Each mode, why it can't run now, and whether it can be chosen, by the daemon's own rules."""
        config = copy.copy(self.config)  # keeps config_path, which resolves relative whisper.cpp paths
        config.openai_api_key = self.api_key
        result = []
        for mode in MODES:
            problem = mode_problem(config, mode)
            result.append(ModeChoice(mode, LABELS[mode], problem, mode == self.config.mode or problem is None))
        return result

    def languages(self) -> tuple[list[Choice], int]:
        """The language popup's rows and the selected one; a code in the file that isn't listed is shown as written."""
        choices = [Choice(name, code) for code, name in LANGUAGES]
        current = self.config.whisper_language or None
        for row, choice in enumerate(choices):
            if choice.value == current:
                return choices, row
        choices.append(Choice(current, current))
        return choices, len(choices) - 1

    def choose_language(self, row: int) -> str | None:
        choices, selected = self.languages()
        return None if row == selected else self.set_language(choices[row].value)

    @property
    def whisper_binary(self) -> str:
        return self.config.whisper_cpp_binary

    @property
    def whisper_model(self) -> str:
        return self.config.whisper_cpp_model
