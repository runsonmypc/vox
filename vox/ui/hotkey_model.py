"""What the hotkey window shows and does, independent of toolkit.

The window records the key to tap on its own (``[hotkey] key``) and an optional key
combination (``[hotkey] fallback``), named as the hotkey listener names them. The listener
does not suppress keys, so the app in front receives them too. Saving writes ``[hotkey]``
to config.toml, which the daemon applies when the window closes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import Config, load_config, update_hotkey
from ..errors import ConfigError
from ..hotkey import parse_combo, resolve_key

MAC = sys.platform == "darwin"
DEFAULT_KEY = Config.hotkey  # "right_shift"; the dataclass default, not a new one

# What both windows say, so macOS and Linux never drift apart
TITLE = "Hotkey"
INTRO = "Tap the hotkey on its own to start dictation, and tap it again to stop and paste. Double-tap it to cancel."
KEY_ROW = "Hotkey"
COMBINATION_ROW = "Key combination"
COMBINATION_NOTE = (
    "Optional. Also starts and stops dictation, and cancels when pressed twice quickly. "
    "The app you’re in receives it too."
)
CLEAR_TOOLTIP = "Remove the key combination"
NO_COMBINATION = "None"
PRESS_KEY = "Press a key…"
PRESS_KEYS = "Press keys…"
USE_DEFAULT = "Use Default"
SAVE_FAILED_TITLE = "Couldn’t Save the Hotkey"
LOAD_FAILED_TITLE = "Couldn’t Read Your Settings"

_MODIFIER_NAMES = "Control, Option or Command" if MAC else "Ctrl, Alt or Super"
KEY_HINT = "Tap the key you want on its own. Esc keeps the current one."
COMBINATION_HINT = f"Hold {_MODIFIER_NAMES}, and press Space or a function key. Esc keeps the current one."
TYPING_KEY = (
    "Vox Transfer needs a key you don’t type with: Shift, Control, Option or Command on either side, "
    "or a function key, F1 to F20."
    if MAC else
    "Vox Transfer needs a key you don’t type with: Shift, Ctrl, Alt or Super on either side, AltGr, "
    "or a function key, F1 to F20."
)
NOT_ALONE = "The hotkey is a single key tapped on its own. Set a combination under Key combination."
BAD_COMBINATION = f"A combination holds {_MODIFIER_NAMES}, and ends with Space or a function key, F1 to F20."

_FUNCTION_KEYS = tuple(f"f{n}" for n in range(1, 21))
_F_KEY_WARNING = (
    "Most Mac keyboards send F1 to F12 only while fn is held, unless “Use F1, F2, etc. keys as standard "
    "function keys” is on in System Settings > Keyboard. Apps receive the key too."
    if MAC else
    "Apps receive F1 to F12 too, and some use them: F5 reloads a web page, for one."
)
_SUPER_WARNING = "GNOME and KDE open their overview when Super is tapped on its own."
_ALT_WARNING = "Some apps, such as Firefox, show their menu bar when Alt is tapped on its own."

# NSEvent key codes -> the names the listener reports for those keys on macOS (pynput's darwin Key values)
MAC_KEYS = {
    60: "right_shift", 56: "shift", 62: "right_ctrl", 59: "ctrl", 61: "right_alt", 58: "alt",
    54: "cmd_r", 55: "cmd", 49: "space",
    122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7", 100: "f8", 101: "f9", 109: "f10",
    103: "f11", 111: "f12", 105: "f13", 107: "f14", 113: "f15", 106: "f16", 64: "f17", 79: "f18", 80: "f19", 90: "f20",
}
# GTK key value names (X keysyms) -> the names the listener reports for those keys on Linux
LINUX_KEYS = {
    "Shift_R": "right_shift", "Shift_L": "shift", "Control_R": "right_ctrl", "Control_L": "ctrl",
    "Alt_R": "right_alt", "Alt_L": "alt", "Super_R": "cmd_r", "Super_L": "cmd",
    # Right Alt on layouts that type with it (AltGr): pynput has no Key for it, so the listener names its keysym
    "ISO_Level3_Shift": "vk_65027",
    "space": "space", **{f"F{n}": f"f{n}" for n in range(1, 21)},
}
_MAC_LABELS = {
    "right_shift": "Right Shift", "shift": "Left Shift", "right_ctrl": "Right Control", "ctrl": "Left Control",
    "right_alt": "Right Option", "alt": "Left Option", "cmd_r": "Right Command", "cmd": "Left Command",
}
_LINUX_LABELS = {
    "right_shift": "Right Shift", "shift": "Left Shift", "right_ctrl": "Right Ctrl", "ctrl": "Left Ctrl",
    "right_alt": "Right Alt", "alt": "Left Alt", "cmd_r": "Right Super", "cmd": "Left Super", "vk_65027": "AltGr",
}
LABELS = {**(_MAC_LABELS if MAC else _LINUX_LABELS), "space": "Space", **{f"f{n}": f"F{n}" for n in range(1, 21)}}
MODIFIERS = frozenset(_MAC_LABELS if MAC else _LINUX_LABELS)
TAP_KEYS = MODIFIERS | set(_FUNCTION_KEYS)
COMBINATION_KEYS = {"space", *_FUNCTION_KEYS}
# The modifiers a combination may hold, in the order it is saved
_ORDER = ("ctrl", "right_ctrl", "alt", "right_alt", "shift", "right_shift", "cmd", "cmd_r")


def label(name: str) -> str:
    """What a key is called on this platform; a name the window doesn't know shows as written."""
    return LABELS.get(resolve_key(name), name)


def combination_label(combo: str) -> str:
    if not combo:
        return NO_COMBINATION
    names = sorted(parse_combo(combo), key=lambda name: _ORDER.index(name) if name in _ORDER else len(_ORDER))
    return " + ".join(label(name) for name in names)


class Capture:
    """The keys pressed while a field records, until they make a hotkey: pressing a key that isn't a
    modifier completes it at once, and so does letting go of a modifier.

    Keys are the listener's names, or None for a key the window doesn't know. A modifier that was
    already down when recording started is ignored.
    """

    def __init__(self) -> None:
        self.held: list[str] = []  # modifiers down now, in press order
        self.pressed: list[str] = []  # modifiers pressed since recording started

    def press(self, name: str | None) -> tuple | None:
        if name not in MODIFIERS:
            return (*self.held, name)
        if name not in self.held:
            self.held.append(name)
        if name not in self.pressed:
            self.pressed.append(name)
        return None

    def release(self, name: str | None) -> tuple | None:
        if name not in self.held:
            return None
        return tuple(self.pressed)

    @property
    def preview(self) -> str:
        """The modifiers held so far, such as "Left Control + …", or ""."""
        return " + ".join([*(label(name) for name in self.held), "…"]) if self.held else ""


class HotkeyModel:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.key = self._saved_key = DEFAULT_KEY
        self.combination = self._saved_combination = ""  # as written in the file, until the user records one
        self.load_error: str | None = None

    @property
    def shown_path(self) -> str:
        return str(self.path).replace(str(Path.home()), "~", 1)

    @property
    def unreadable(self) -> str:
        return f"Couldn’t read {self.shown_path}. Fix the file, then reopen this window."

    def reload(self) -> None:
        """Read the hotkey settings. On failure, set ``load_error``: the window then saves nothing."""
        try:
            config = load_config(self.path)
        except Exception as e:
            self.load_error = str(e)
            return
        self.load_error = None
        self.key = self._saved_key = config.hotkey
        self.combination = self._saved_combination = config.hotkey_fallback

    @property
    def changed(self) -> bool:
        return (self.key, self.combination) != (self._saved_key, self._saved_combination)

    @property
    def is_default(self) -> bool:
        return resolve_key(self.key) == DEFAULT_KEY and not self.combination

    @property
    def problem(self) -> str | None:
        """Why the settings can't be saved: the listener never sees a combination that includes the hotkey."""
        key = resolve_key(self.key)
        if self.combination and key in parse_combo(self.combination):
            return f"The key combination can’t include the hotkey, {label(key)}."
        return None

    @property
    def warning(self) -> str | None:
        """Something to know about the hotkey that still allows saving it."""
        key = resolve_key(self.key)
        if key in _FUNCTION_KEYS[:12]:
            return _F_KEY_WARNING
        if not MAC and key == "cmd":
            return _SUPER_WARNING
        if not MAC and key in ("alt", "right_alt"):
            return _ALT_WARNING
        return None

    def status(self, recording: str | None, refusal: str | None) -> tuple[str, str | None]:
        """The status line and its kind, "error", "warning" or "hint": while a field records ("key" or
        "combination"), its last refusal or its hint; otherwise a clash, then a warning. "" hides it."""
        if recording is not None:
            if refusal:
                return refusal, "error"
            return (KEY_HINT if recording == "key" else COMBINATION_HINT), "hint"
        if self.problem:
            return self.problem, "error"
        if self.warning:
            return self.warning, "warning"
        return "", None

    def record_key(self, names: tuple) -> str | None:
        """Use the keys a Capture completed as the hotkey; returns why they can't be, or None."""
        if len(names) > 1:
            return NOT_ALONE
        if names[0] not in TAP_KEYS:
            return TYPING_KEY
        self.key = names[0]
        return None

    def record_combination(self, names: tuple) -> str | None:
        """Use the keys a Capture completed as the key combination; returns why they can't be, or None."""
        *modifiers, last = names
        usable = (
            modifiers and set(modifiers) <= set(_ORDER)
            and not set(modifiers) <= {"shift", "right_shift"}  # Shift with Space happens while typing
            and last in COMBINATION_KEYS
        )
        if not usable:
            return BAD_COMBINATION
        self.combination = "+".join([name for name in _ORDER if name in modifiers] + [last])
        return None

    def clear_combination(self) -> None:
        self.combination = ""

    def use_default(self) -> None:
        self.key, self.combination = DEFAULT_KEY, ""

    def save(self) -> None:
        """Write [hotkey] if it changed. Raises ConfigError while the file doesn't load, and ValueError on a clash."""
        if self.load_error is not None:
            raise ConfigError(self.load_error)
        if self.problem is not None:
            raise ValueError(self.problem)
        if not self.changed:
            return  # so no config.toml is created for a user who has none
        update_hotkey(self.path, self.key, self.combination)
        self._saved_key, self._saved_combination = self.key, self.combination
