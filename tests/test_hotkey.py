"""Unit tests for HotkeyListener double-tap detection and cancel events."""

import asyncio
from unittest.mock import patch
import pytest
from pynput import keyboard

from vox.config import Config
from vox.hotkey import HotkeyListener


class ControlledClock:
    """Helper to advance simulated time monotonically."""

    def __init__(self, initial_time: float = 100.0) -> None:
        self._current_time = initial_time

    def time(self) -> float:
        return self._current_time

    def advance(self, seconds: float) -> None:
        self._current_time += seconds


@pytest.mark.anyio
async def test_hotkey_single_tap_emits_toggle():
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", double_tap_timeout_ms=400)
    loop = asyncio.get_running_loop()
    listener = HotkeyListener(config, loop, queue)

    with patch("time.monotonic", side_effect=clock.time):
        # Press right_shift
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)  # 100ms hold (>= 80ms min_hold_ms)
        listener._on_release(keyboard.Key.shift_r)

    await asyncio.sleep(0)
    assert queue.qsize() == 1
    event = await queue.get()
    assert event == "toggle"


@pytest.mark.anyio
async def test_hotkey_double_tap_within_timeout_emits_cancel():
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", double_tap_timeout_ms=400)
    loop = asyncio.get_running_loop()
    listener = HotkeyListener(config, loop, queue)

    with patch("time.monotonic", side_effect=clock.time):
        # Tap 1
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)
        listener._on_release(keyboard.Key.shift_r)

        # Gap of 100ms
        clock.advance(0.1)

        # Tap 2 (total elapsed since tap 1 release is 0.1 + 0.1 = 200ms <= 400ms)
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)
        listener._on_release(keyboard.Key.shift_r)

    await asyncio.sleep(0)
    assert queue.qsize() == 2
    event1 = await queue.get()
    event2 = await queue.get()
    assert event1 == "toggle"
    assert event2 == "cancel"


@pytest.mark.anyio
async def test_hotkey_double_tap_outside_timeout_emits_two_toggles():
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", double_tap_timeout_ms=400)
    loop = asyncio.get_running_loop()
    listener = HotkeyListener(config, loop, queue)

    with patch("time.monotonic", side_effect=clock.time):
        # Tap 1
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)
        listener._on_release(keyboard.Key.shift_r)

        # Wait 500ms (> 400ms)
        clock.advance(0.5)

        # Tap 2
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)
        listener._on_release(keyboard.Key.shift_r)

    await asyncio.sleep(0)
    assert queue.qsize() == 2
    event1 = await queue.get()
    event2 = await queue.get()
    assert event1 == "toggle"
    assert event2 == "toggle"


@pytest.mark.anyio
async def test_hotkey_intervening_key_resets_double_tap():
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", double_tap_timeout_ms=400)
    loop = asyncio.get_running_loop()
    listener = HotkeyListener(config, loop, queue)

    with patch("time.monotonic", side_effect=clock.time):
        # Tap 1
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)
        listener._on_release(keyboard.Key.shift_r)

        # Intervening key press
        clock.advance(0.05)
        listener._on_press(keyboard.KeyCode.from_char("a"))
        clock.advance(0.05)
        listener._on_release(keyboard.KeyCode.from_char("a"))

        # Tap 2 within 400ms of Tap 1 release, but intervening key was pressed
        clock.advance(0.05)
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)
        listener._on_release(keyboard.Key.shift_r)

    await asyncio.sleep(0)
    assert queue.qsize() == 2
    event1 = await queue.get()
    event2 = await queue.get()
    assert event1 == "toggle"
    assert event2 == "toggle"  # Not cancel because 'a' reset the tracking!


@pytest.mark.anyio
async def test_hotkey_triple_tap_behavior():
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", double_tap_timeout_ms=400)
    loop = asyncio.get_running_loop()
    listener = HotkeyListener(config, loop, queue)

    with patch("time.monotonic", side_effect=clock.time):
        # Tap 1 -> toggle
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)
        listener._on_release(keyboard.Key.shift_r)

        clock.advance(0.1)

        # Tap 2 -> cancel
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)
        listener._on_release(keyboard.Key.shift_r)

        clock.advance(0.1)

        # Tap 3 -> new toggle (double tap was reset by cancel)
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.1)
        listener._on_release(keyboard.Key.shift_r)

    await asyncio.sleep(0)
    assert queue.qsize() == 3
    assert await queue.get() == "toggle"
    assert await queue.get() == "cancel"
    assert await queue.get() == "toggle"


@pytest.mark.anyio
async def test_hotkey_held_with_other_key_ignored():
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", double_tap_timeout_ms=400)
    loop = asyncio.get_running_loop()
    listener = HotkeyListener(config, loop, queue)

    with patch("time.monotonic", side_effect=clock.time):
        # Shift held down
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.05)
        # Type 'a' while shift held
        listener._on_press(keyboard.KeyCode.from_char("a"))
        clock.advance(0.05)
        listener._on_release(keyboard.KeyCode.from_char("a"))
        clock.advance(0.05)
        # Release shift
        listener._on_release(keyboard.Key.shift_r)

    await asyncio.sleep(0)
    assert queue.qsize() == 0


@pytest.mark.anyio
async def test_hotkey_fallback_combo_double_tap():
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", hotkey_fallback="ctrl+space", double_tap_timeout_ms=400)
    loop = asyncio.get_running_loop()
    listener = HotkeyListener(config, loop, queue)

    with patch("time.monotonic", side_effect=clock.time):
        # First combo
        listener._on_press(keyboard.Key.ctrl_l)
        listener._on_press(keyboard.Key.space)
        listener._on_release(keyboard.Key.space)
        listener._on_release(keyboard.Key.ctrl_l)

        clock.advance(0.15)

        # Second combo within 400ms
        listener._on_press(keyboard.Key.ctrl_l)
        listener._on_press(keyboard.Key.space)
        listener._on_release(keyboard.Key.space)
        listener._on_release(keyboard.Key.ctrl_l)

    await asyncio.sleep(0)
    assert queue.qsize() == 2
    assert await queue.get() == "toggle"
    assert await queue.get() == "cancel"


@pytest.mark.anyio
async def test_hotkey_quick_tap_responsive():
    """Verify that quick 40ms key tap triggers toggle (no dropped quick taps)."""
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", double_tap_timeout_ms=400)
    loop = asyncio.get_running_loop()
    listener = HotkeyListener(config, loop, queue)

    with patch("time.monotonic", side_effect=clock.time):
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.04)  # 40ms hold (>= 30ms min_hold_ms)
        listener._on_release(keyboard.Key.shift_r)

    await asyncio.sleep(0)
    assert queue.qsize() == 1
    assert await queue.get() == "toggle"

