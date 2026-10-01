"""Global hotkey listener using pynput — runs in a daemon thread."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from threading import Lock
from time import monotonic

from .config import Config
from .errors import DependencyError


def _import_problem(e: ImportError) -> str:
    """pynput's import error in a few words; its message nests the X error's repr."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return "no X display: DISPLAY is not set"
    first = (str(e).splitlines() or [type(e).__name__])[0]
    inner = re.search(r"\('([^']+)'", first)
    return inner.group(1) if inner else first


try:
    from pynput import keyboard
except ImportError as e:  # the X11 backend connects to the display on import
    keyboard = None
    _IMPORT_ERROR = _import_problem(e)

if sys.platform == "darwin":
    from Quartz import CGEventSourceFlagsState, kCGEventFlagMaskSecondaryFn, kCGEventSourceStateHIDSystemState

log = logging.getLogger(__name__)

# pynput names for the right-hand modifiers -> the names Vox's config uses
_KEY_NAMES = {"shift_r": "right_shift", "ctrl_r": "right_ctrl", "alt_r": "right_alt"}
# Other spellings a config may use. pynput reports the left modifiers by their bare names
# (Key.shift_l is an alias of Key.shift), so "left_shift" has to mean "shift".
_ALIASES = {
    "left_shift": "shift", "left_ctrl": "ctrl", "left_alt": "alt",
    "shift_l": "shift", "ctrl_l": "ctrl", "alt_l": "alt",
    "globe": "fn",
    **_KEY_NAMES,
}
# X11 reports the Alt keys as Meta_L and Meta_R while Shift is held; naming them Alt
# keeps an Alt released after Shift from staying down in a combination
_META_KEYSYMS = {0xFFE7: "alt", 0xFFE8: "right_alt"}
# macOS: the key code of fn (Globe), which pynput has no Key for
_FN_VK = 63


def resolve_key(name: str) -> str:
    """Normalize a configured key name to the name the listener reports: "Right Shift" -> "right_shift"."""
    name = name.strip().lower().replace(" ", "_")
    return _ALIASES.get(name, name)


def _fn_down() -> bool:
    """Whether fn is down now, by the HID system's modifier flags.

    pynput's darwin listener has no flag for fn, so it reports fn going down as a release too.
    Asking for the current state at every fn event, instead of flipping a remembered one, means
    a missed event can't leave fn down.
    """
    return bool(CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState) & kCGEventFlagMaskSecondaryFn)


def parse_combo(combo: str) -> set[str]:
    """Parse 'ctrl+space' into a set of key names."""
    return {resolve_key(part) for part in combo.split("+")}


class HotkeyListener:
    """Listens for the toggle hotkey and posts events to an asyncio queue.

    For right_shift (or other modifier keys used solo):
    - Tracks press/release
    - Only fires toggle if no other key was pressed between press and release
    - 50ms debounce on release
    """

    def __init__(self, config: Config, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        if keyboard is None:
            raise DependencyError(
                f"Global hotkeys are unavailable ({_IMPORT_ERROR}). Vox Transfer needs an X11 display; "
                "on a Wayland-only session, log in with an Xorg session instead."
            )
        self._loop = loop
        self._queue = queue
        self._hotkey_name = resolve_key(config.hotkey)
        self._fallback = config.hotkey_fallback
        self._double_tap_cancel = config.double_tap_cancel
        self._double_tap_timeout_ms = config.double_tap_timeout_ms
        self._listener: keyboard.Listener | None = None

        # State for solo-modifier detection
        self._lock = Lock()
        self._modifier_pressed = False
        self._other_key_pressed = False
        self._press_time: float = 0
        self._last_toggle_time: float = 0
        self._last_release_time: float = 0.0
        self._last_fallback_time: float = 0.0
        self._debounce_ms = 50
        # filter out synthetic/phantom key events (< 30ms) while keeping quick taps responsive; PS/2 keyboards
        # send Pause's press and release together, so a Pause tap has no hold to measure
        self._min_hold_ms = 0 if self._hotkey_name == "pause" else 30

        self._fallback_keys = parse_combo(self._fallback) if self._fallback else None
        self._combo_state: set[str] = set()

    def start(self) -> None:
        """Start the listener in a daemon thread."""
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        log.info("Hotkey listener started (key=%s, fallback=%s)", self._hotkey_name, self._fallback)

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def _fire_toggle(self, source: str) -> None:
        now = monotonic()
        if (now - self._last_toggle_time) * 1000 < self._debounce_ms:
            return
        self._last_toggle_time = now
        self._loop.call_soon_threadsafe(self._queue.put_nowait, "toggle")
        log.info("Toggle fired via %s", source)

    def _fire_cancel(self, source: str) -> None:
        now = monotonic()
        self._last_toggle_time = now
        self._loop.call_soon_threadsafe(self._queue.put_nowait, "cancel")
        log.info("Cancel fired via %s", source)

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        key_name = self._key_name(key)

        # Solo modifier detection
        if key_name == self._hotkey_name:
            with self._lock:
                if self._modifier_pressed and key_name != "fn":
                    return  # ignore auto-repeat; fn never repeats, so fn down again means its release was missed
                self._modifier_pressed = True
                self._other_key_pressed = False
                self._press_time = monotonic()
            return

        # Track combo keys for fallback
        if self._fallback_keys:
            self._combo_state.add(key_name)
            if self._fallback_keys <= self._combo_state:
                now = monotonic()
                with self._lock:
                    if self._modifier_pressed:
                        self._other_key_pressed = True  # the tap-alone key was held for this combination
                    if (self._double_tap_cancel and self._last_fallback_time > 0
                            and (now - self._last_fallback_time) * 1000 <= self._double_tap_timeout_ms):
                        self._last_fallback_time = 0.0
                        is_cancel = True
                    else:
                        self._last_fallback_time = now
                        is_cancel = False
                    self._last_release_time = 0.0
                if is_cancel:
                    self._fire_cancel(f"fallback combo double-tap (keys: {self._combo_state})")
                else:
                    self._fire_toggle(f"fallback combo (keys: {self._combo_state})")
                self._combo_state.clear()
                return

        # Any other key resets double-tap sequence tracking
        with self._lock:
            if self._modifier_pressed:
                self._other_key_pressed = True
            self._last_release_time = 0.0
            if not (self._fallback_keys and key_name in self._fallback_keys):
                self._last_fallback_time = 0.0

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        key_name = self._key_name(key)
        # pynput reports fn going down as a release too. Unless fn is the hotkey, its events are left alone:
        # Mac laptops hold fn for the function keys, and fn as another key would break an F-key double-tap
        if key_name == "fn" and self._hotkey_name == "fn" and _fn_down():
            self._on_press(key)
            return

        if key_name == self._hotkey_name:
            now = monotonic()
            with self._lock:
                was_solo = self._modifier_pressed and not self._other_key_pressed
                held_ms = (now - self._press_time) * 1000
                self._modifier_pressed = False
                if was_solo and held_ms >= self._min_hold_ms:
                    if (self._double_tap_cancel and self._last_release_time > 0
                            and (now - self._last_release_time) * 1000 <= self._double_tap_timeout_ms):
                        self._last_release_time = 0.0
                        is_cancel = True
                    else:
                        self._last_release_time = now
                        is_cancel = False
                else:
                    is_cancel = False

            if was_solo and held_ms >= self._min_hold_ms:
                if is_cancel:
                    self._fire_cancel(f"solo {self._hotkey_name} double-tap ({held_ms:.0f}ms)")
                else:
                    self._fire_toggle(f"solo {self._hotkey_name} ({held_ms:.0f}ms)")
            elif was_solo:
                log.debug("Ignoring short press: %.0fms (min %dms)", held_ms, self._min_hold_ms)
            return

        # Remove from combo state
        self._combo_state.discard(key_name)

    @staticmethod
    def _key_name(key: keyboard.Key | keyboard.KeyCode | None) -> str:
        """Get normalized name of a key."""
        if key is None:
            return ""
        if isinstance(key, keyboard.Key):
            # e.g. Key.shift_r -> "right_shift"
            return _KEY_NAMES.get(key.name, key.name)
        if isinstance(key, keyboard.KeyCode):
            if sys.platform == "darwin" and key.vk == _FN_VK:
                return "fn"
            if key.char:
                return key.char.lower()
            if key.vk:
                return _META_KEYSYMS.get(key.vk, f"vk_{key.vk}")
        return ""
