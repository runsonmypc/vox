"""Unit tests for HotkeyListener double-tap detection and cancel events."""

import asyncio
import sys
from unittest.mock import patch

import pytest
from pynput import keyboard

from vox import hotkey
from vox.config import Config
from vox.errors import DependencyError
from vox.hotkey import HotkeyListener, resolve_key


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

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
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

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
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

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
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

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
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

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
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

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
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

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
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

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
        listener._on_press(keyboard.Key.shift_r)
        clock.advance(0.04)  # 40ms hold (>= 30ms min_hold_ms)
        listener._on_release(keyboard.Key.shift_r)

    await asyncio.sleep(0)
    assert queue.qsize() == 1
    assert await queue.get() == "toggle"



def _tap(listener, clock, key):
    listener._on_press(key)
    clock.advance(0.1)
    listener._on_release(key)
    clock.advance(1.0)  # well outside the double-tap window


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("configured", "key"),
    [
        ("right_shift", keyboard.Key.shift_r),
        ("Right Shift", keyboard.Key.shift_r),
        ("Right_Shift", keyboard.Key.shift_r),
        (" right_ctrl ", keyboard.Key.ctrl_r),
        ("alt_r", keyboard.Key.alt_r),
        ("left_shift", keyboard.Key.shift_l),
        ("shift_l", keyboard.Key.shift_l),
        ("left_ctrl", keyboard.Key.ctrl_l),
        ("left_alt", keyboard.Key.alt_l),
        ("f13", keyboard.Key.f13),
    ],
)
async def test_hotkey_spellings_match_the_key_pynput_reports(configured, key):
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    listener = HotkeyListener(Config(hotkey=configured), asyncio.get_running_loop(), queue)

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
        _tap(listener, clock, key)

    await asyncio.sleep(0)
    assert queue.qsize() == 1
    assert await queue.get() == "toggle"


@pytest.mark.anyio
async def test_left_shift_hotkey_ignores_the_right_shift():
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    listener = HotkeyListener(Config(hotkey="left_shift"), asyncio.get_running_loop(), queue)

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
        _tap(listener, clock, keyboard.Key.shift_r)

    await asyncio.sleep(0)
    assert queue.empty()


@pytest.mark.anyio
async def test_hotkey_fallback_combo_accepts_left_modifier_names():
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", hotkey_fallback="Left_Ctrl+Space")
    listener = HotkeyListener(config, asyncio.get_running_loop(), queue)

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
        listener._on_press(keyboard.Key.ctrl_l)
        listener._on_press(keyboard.Key.space)
        listener._on_release(keyboard.Key.space)
        listener._on_release(keyboard.Key.ctrl_l)

    await asyncio.sleep(0)
    assert await queue.get() == "toggle"


META_L, META_R = keyboard.KeyCode.from_vk(0xFFE7), keyboard.KeyCode.from_vk(0xFFE8)  # X11: Alt with Shift held


@pytest.mark.anyio
async def test_alt_with_shift_held_is_still_alt():
    queue: asyncio.Queue[str] = asyncio.Queue()
    config = Config(hotkey="right_shift", hotkey_fallback="alt+space")
    listener = HotkeyListener(config, asyncio.get_running_loop(), queue)

    # Alt let go while Shift is still down doesn't stay held, so a later Space alone does nothing
    listener._on_press(keyboard.Key.alt_l)
    listener._on_press(keyboard.Key.shift)
    listener._on_release(META_L)
    listener._on_release(keyboard.Key.shift)
    listener._on_press(keyboard.Key.space)
    listener._on_release(keyboard.Key.space)
    await asyncio.sleep(0)
    assert queue.empty()

    # and Alt pressed after Shift completes a combination that holds both
    listener = HotkeyListener(Config(hotkey_fallback="alt+shift+space"), asyncio.get_running_loop(), queue)
    listener._on_press(keyboard.Key.shift)
    listener._on_press(META_L)
    listener._on_press(keyboard.Key.space)
    await asyncio.sleep(0)
    assert await queue.get() == "toggle"
    assert HotkeyListener._key_name(META_R) == "right_alt"


@pytest.mark.anyio
@pytest.mark.skipif(not hasattr(keyboard.Key, "pause"), reason="Mac keyboards have no Pause or Scroll Lock")
@pytest.mark.parametrize(("configured", "name"), [("pause", "pause"), ("Scroll Lock", "scroll_lock")])
async def test_pause_and_scroll_lock_toggle_when_tapped(configured, name):
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    listener = HotkeyListener(Config(hotkey=configured), asyncio.get_running_loop(), queue)

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
        _tap(listener, clock, getattr(keyboard.Key, name))

    await asyncio.sleep(0)
    assert await queue.get() == "toggle"


def test_globe_is_another_name_for_fn():
    assert resolve_key("globe") == resolve_key("Globe") == resolve_key(" FN ") == "fn"


# -- fn (Globe) on macOS --------------------------------------------------------------

macos = pytest.mark.skipif(sys.platform != "darwin", reason="fn is a macOS key")
FN = keyboard.KeyCode.from_vk(63)  # pynput has no Key for fn, so it reports fn by its key code


@pytest.fixture
def fn():
    """Move fn as pynput's darwin listener reports it: a release of vk 63 whether fn goes down or
    comes up, with the fn flag in the HID system's state (Quartz, mocked here) telling which."""
    import Quartz

    flags = {"now": 0}

    def flags_state(source):
        assert source == Quartz.kCGEventSourceStateHIDSystemState
        return flags["now"]

    def move(listener, down):
        flags["now"] = Quartz.kCGEventFlagMaskSecondaryFn if down else 0
        listener._on_release(FN)

    with patch("vox.hotkey.CGEventSourceFlagsState", side_effect=flags_state):
        yield move


async def _events(queue):
    await asyncio.sleep(0)
    return [queue.get_nowait() for _ in range(queue.qsize())]


@macos
@pytest.mark.anyio
async def test_fn_tapped_alone_toggles_and_double_tapped_cancels(fn):
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    listener = HotkeyListener(Config(hotkey="globe", double_tap_timeout_ms=400), asyncio.get_running_loop(), queue)

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
        for _ in range(2):
            fn(listener, down=True)
            clock.advance(0.1)
            fn(listener, down=False)
            clock.advance(0.1)

    assert await _events(queue) == ["toggle", "cancel"]


@macos
@pytest.mark.anyio
async def test_fn_held_for_another_key_does_not_toggle(fn):
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    listener = HotkeyListener(Config(hotkey="fn"), asyncio.get_running_loop(), queue)

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
        fn(listener, down=True)
        clock.advance(0.1)
        listener._on_press(keyboard.Key.f5)  # fn+F5 on a Mac laptop
        listener._on_release(keyboard.Key.f5)
        clock.advance(0.1)
        fn(listener, down=False)

    assert await _events(queue) == []


@macos
@pytest.mark.anyio
async def test_a_missed_fn_event_cannot_leave_fn_down(fn):
    clock = ControlledClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    listener = HotkeyListener(Config(hotkey="fn"), asyncio.get_running_loop(), queue)

    with patch("vox.hotkey.monotonic", side_effect=clock.time):
        fn(listener, down=True)  # and its release never arrives
        clock.advance(1.0)
        listener._on_press(keyboard.KeyCode.from_char("a"))
        listener._on_release(keyboard.KeyCode.from_char("a"))
        clock.advance(1.0)
        fn(listener, down=True)  # the next tap works: fn down again starts a new press
        clock.advance(0.1)
        fn(listener, down=False)
        clock.advance(1.0)
        fn(listener, down=False)  # a release whose press was missed fires nothing
        clock.advance(1.0)
        fn(listener, down=True)
        clock.advance(0.1)
        fn(listener, down=False)

    assert await _events(queue) == ["toggle", "toggle"]


@macos
@pytest.mark.anyio
async def test_fn_held_for_a_function_key_keeps_a_combination_working(fn):
    queue: asyncio.Queue[str] = asyncio.Queue()
    listener = HotkeyListener(Config(hotkey_fallback="ctrl+f5"), asyncio.get_running_loop(), queue)

    listener._on_press(keyboard.Key.ctrl)
    fn(listener, down=True)
    listener._on_press(keyboard.Key.f5)

    assert await _events(queue) == ["toggle"]


@pytest.mark.anyio
async def test_missing_keyboard_backend_is_a_clear_dependency_error():
    with patch("vox.hotkey.keyboard", None), \
         patch("vox.hotkey._IMPORT_ERROR", "failed to acquire X connection: Bad display name", create=True):
        with pytest.raises(DependencyError, match="X11 display") as excinfo:
            HotkeyListener(Config(), asyncio.get_running_loop(), asyncio.Queue())
    assert "Bad display name" in str(excinfo.value)


PYNPUT_X_ERROR = (
    "this platform is not supported: ('failed to acquire X connection: Bad display name \"\"', DisplayNameError(''))\n"
    "\n"
    "Try one of the following resolutions:\n"
)


def test_import_problem_without_display_says_so(monkeypatch):
    monkeypatch.setattr("vox.hotkey.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert hotkey._import_problem(ImportError(PYNPUT_X_ERROR)) == "no X display: DISPLAY is not set"


def test_import_problem_takes_the_x_error_out_of_pynputs_message(monkeypatch):
    monkeypatch.setattr("vox.hotkey.sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":7")
    assert hotkey._import_problem(ImportError(PYNPUT_X_ERROR)) == 'failed to acquire X connection: Bad display name ""'
    assert hotkey._import_problem(ImportError("No module named 'Xlib'")) == "No module named 'Xlib'"
    assert hotkey._import_problem(ImportError()) == "ImportError"
