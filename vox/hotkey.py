"""Global hotkey listener using pynput — runs in a daemon thread."""

from __future__ import annotations

import asyncio
import logging
import time
from threading import Lock

from pynput import keyboard

from .config import Config

log = logging.getLogger(__name__)


class HotkeyListener:
    """Listens for the toggle hotkey and posts events to an asyncio queue.

    For right_shift (or other modifier keys used solo):
    - Tracks press/release
    - Only fires toggle if no other key was pressed between press and release
    - 50ms debounce on release
    """

    def __init__(self, config: Config, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        self._loop = loop
        self._queue = queue
        self._hotkey_name = config.hotkey
        self._fallback = config.hotkey_fallback
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
        self._min_hold_ms = 80  # filter out synthetic/phantom key events

        # Resolve key
        self._hotkey_key = self._resolve_key(self._hotkey_name)
        self._fallback_keys = self._parse_combo(self._fallback) if self._fallback else None
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
        now = time.monotonic()
        if (now - self._last_toggle_time) * 1000 < self._debounce_ms:
            return
        self._last_toggle_time = now
        self._loop.call_soon_threadsafe(self._queue.put_nowait, "toggle")
        log.info("Toggle fired via %s", source)

    def _fire_cancel(self, source: str) -> None:
        now = time.monotonic()
        self._last_toggle_time = now
        self._loop.call_soon_threadsafe(self._queue.put_nowait, "cancel")
        log.info("Cancel fired via %s", source)

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        key_name = self._key_name(key)

        # Solo modifier detection
        if key_name == self._hotkey_name:
            with self._lock:
                if self._modifier_pressed:
                    return  # ignore auto-repeat
                self._modifier_pressed = True
                self._other_key_pressed = False
                self._press_time = time.monotonic()
            return

        # Track combo keys for fallback
        if self._fallback_keys:
            self._combo_state.add(key_name)
            if self._fallback_keys <= self._combo_state:
                now = time.monotonic()
                with self._lock:
                    if self._last_fallback_time > 0 and (now - self._last_fallback_time) * 1000 <= self._double_tap_timeout_ms:
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

        if key_name == self._hotkey_name:
            now = time.monotonic()
            with self._lock:
                was_solo = self._modifier_pressed and not self._other_key_pressed
                held_ms = (now - self._press_time) * 1000
                self._modifier_pressed = False
                if was_solo and held_ms >= self._min_hold_ms:
                    if self._last_release_time > 0 and (now - self._last_release_time) * 1000 <= self._double_tap_timeout_ms:
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
    def _resolve_key(name: str) -> str:
        """Normalize a key name."""
        return name.lower().replace(" ", "_")

    @staticmethod
    def _parse_combo(combo: str) -> set[str]:
        """Parse 'ctrl+space' into a set of key names."""
        return {part.strip().lower().replace(" ", "_") for part in combo.split("+")}

    @staticmethod
    def _key_name(key: keyboard.Key | keyboard.KeyCode | None) -> str:
        """Get normalized name of a key."""
        if key is None:
            return ""
        if isinstance(key, keyboard.Key):
            # e.g. Key.shift_r -> "right_shift"
            name = key.name
            # Normalize pynput names to our format
            remap = {
                "shift_r": "right_shift",
                "shift_l": "left_shift",
                "ctrl_r": "right_ctrl",
                "ctrl_l": "left_ctrl",
                "alt_r": "right_alt",
                "alt_l": "left_alt",
            }
            return remap.get(name, name)
        if isinstance(key, keyboard.KeyCode):
            if key.char:
                return key.char.lower()
            if key.vk:
                return f"vk_{key.vk}"
        return ""
