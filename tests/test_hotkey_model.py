"""The Hotkey page's model; the page itself is tested in test_settings_window.py."""

import sys
import tomllib

import pytest

from vox import hotkey
from vox.config import load_config
from vox.errors import ConfigError
from vox.hotkey import HotkeyListener, parse_combo, resolve_key
from vox.ui.hotkey_model import (
    BAD_COMBINATION,
    DEFAULT_KEY,
    KEY_HINT,
    LINUX_KEYS,
    MAC,
    MAC_KEYS,
    MODIFIERS,
    NOT_ALONE,
    TYPING_KEY,
    Capture,
    HotkeyModel,
    combination_label,
    label,
)

CONFIG = """\
# my settings
[hotkey]
key = "right_shift"  # the default
fallback = "Left_Ctrl+Space"
double_tap_timeout_ms = 300

[audio]
sample_rate = 16000  # keep this comment
"""


def loaded(path):
    model = HotkeyModel(path)
    model.reload()
    return model


@pytest.fixture
def model(tmp_path):
    return loaded(tmp_path / "config.toml")


# -- The listener's key names ---------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS key codes")
def test_mac_key_codes_name_the_keys_the_listener_reports():
    keyboard = hotkey.keyboard
    for code, name in MAC_KEYS.items():
        key = next(
            (k for k in keyboard.Key if k.value.vk == code and not getattr(k.value, "_is_media", False)), None
        ) or keyboard.KeyCode.from_vk(code)  # fn, which pynput has no Key for
        assert HotkeyListener._key_name(key) == name, code


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="X keysyms")
def test_linux_key_names_name_the_keys_the_listener_reports():
    keyboard = hotkey.keyboard
    if keyboard is None:
        pytest.skip("pynput needs an X display")
    from Xlib import XK

    for keysym_name, name in LINUX_KEYS.items():
        keysym = XK.string_to_keysym(keysym_name)
        key = next((k for k in keyboard.Key if k.value.vk == keysym), None) or keyboard.KeyCode.from_vk(keysym)
        assert HotkeyListener._key_name(key) == name, keysym_name


def test_recorded_names_read_back_unchanged(model):
    for name in {*MAC_KEYS.values(), *LINUX_KEYS.values()}:
        assert resolve_key(name) == name
    names = ("ctrl", "right_ctrl", "alt", "right_alt", "shift", "right_shift", "cmd", "cmd_r", "f20")
    assert model.record_combination(names) is None
    assert parse_combo(model.combination) == set(names)


# -- Capture --------------------------------------------------------------------------


def test_a_modifier_tapped_alone_is_recorded_when_released():
    capture = Capture()
    assert capture.press("right_shift") is None
    assert capture.release("right_shift") == ("right_shift",)


def test_a_combination_is_recorded_when_its_last_key_is_pressed():
    capture = Capture()
    capture.press("ctrl")
    capture.press("shift")
    assert capture.press("space") == ("ctrl", "shift", "space")
    assert Capture().press("f13") == ("f13",)
    assert Capture().press(None) == (None,)  # a key the window doesn't know


def test_modifiers_alone_are_recorded_together():
    capture = Capture()
    capture.press("ctrl")
    capture.press("alt")
    assert capture.release("alt") == ("ctrl", "alt")


def test_a_key_held_before_recording_is_ignored():
    capture = Capture()
    assert capture.release("ctrl") is None  # down since before the field was clicked
    assert capture.press("space") == ("space",)


def test_fn_tapped_alone_is_recorded_as_fn():
    capture = Capture()
    assert capture.press("fn") is None
    assert capture.preview == ""
    assert capture.release("fn") == ("fn",)


def test_fn_held_with_another_key_records_only_that_key():
    capture = Capture()
    capture.press("fn")
    assert capture.press("f5") == ("f5",)  # Mac laptops need fn held for the function keys
    assert capture.release("fn") is None

    capture = Capture()
    capture.press("fn")
    capture.press("cmd_r")
    assert capture.release("cmd_r") == ("cmd_r",)

    capture = Capture()
    capture.press("ctrl")
    capture.press("fn")
    assert capture.release("fn") is None
    assert capture.preview == f"{label('ctrl')} + …"
    assert capture.press("space") == ("ctrl", "space")


def test_the_combination_field_shows_the_held_modifiers():
    capture = Capture()
    assert capture.preview == ""
    capture.press("ctrl")
    assert capture.preview == f"{label('ctrl')} + …"
    capture.press("cmd_r")
    assert capture.preview == f"{label('ctrl')} + {label('cmd_r')} + …"


# -- What may be recorded ---------------------------------------------------------------


def test_modifiers_and_function_keys_can_be_the_hotkey(model):
    for name in [*MODIFIERS, "f1", "f13", "f20"]:
        assert model.record_key((name,)) is None
        assert model.key == name
    assert label("cmd_r") == ("Right Command" if MAC else "Right Super")


def test_fn_on_macos_and_pause_and_scroll_lock_on_linux_can_be_the_hotkey(model):
    ours, theirs = (("fn",), ("pause", "scroll_lock")) if MAC else (("pause", "scroll_lock"), ("fn",))
    for name in ours:
        assert model.record_key((name,)) is None
        assert model.key == name
    for name in theirs:
        assert model.record_key((name,)) == TYPING_KEY
    if MAC:
        assert label("fn") == label("globe") == "fn (Globe)"
    else:
        assert (label("pause"), label("scroll_lock")) == ("Pause", "Scroll Lock")


@pytest.mark.parametrize("name", [None, "space", "a", "1", "enter", "tab", "esc", "caps_lock", "vk_63", "up"])
def test_typing_keys_cannot_be_the_hotkey(model, name):
    assert model.record_key((name,)) == TYPING_KEY
    assert model.key == DEFAULT_KEY


def test_a_combination_pressed_for_the_hotkey_points_to_its_own_field(model):
    assert model.record_key(("ctrl", "space")) == NOT_ALONE
    assert model.record_key(("ctrl", "shift")) == NOT_ALONE
    assert model.key == DEFAULT_KEY


@pytest.mark.parametrize("names, saved", [
    (("ctrl", "space"), "ctrl+space"),
    (("shift", "space"), None),  # Shift with Space happens while typing
    (("ctrl", None), None),  # a letter
    (("ctrl", "alt"), None),  # modifiers only
    (("f5",), None),  # F5 alone is a hotkey, not a combination
    (("vk_65027", "space"), None),  # AltGr
    (("fn",), None),
    (("ctrl", "fn"), None),
    (("fn", "space"), None),
    (("ctrl", "pause"), None),
    (("ctrl", "scroll_lock"), None),
])
def test_a_combination_needs_a_modifier_and_space_or_a_function_key(model, names, saved):
    assert model.record_combination(names) == (None if saved else BAD_COMBINATION)
    assert model.combination == (saved or "")


def test_a_combination_is_saved_in_a_fixed_order(model):
    model.record_combination(("shift", "right_ctrl", "f5"))
    assert model.combination == "right_ctrl+shift+f5"
    model.record_combination(("cmd_r", "alt", "ctrl", "space"))
    assert model.combination == "ctrl+alt+cmd_r+space"
    assert combination_label(model.combination) == " + ".join(label(n) for n in ("ctrl", "alt", "cmd_r", "space"))


def test_the_combination_may_not_include_the_hotkey(tmp_path):
    path = tmp_path / "config.toml"
    model = loaded(path)
    model.record_key(("ctrl",))
    model.record_combination(("ctrl", "space"))
    assert model.problem == f"The key combination can’t include the hotkey, {label('ctrl')}."
    assert model.status(None, None) == (model.problem, "error")
    with pytest.raises(ValueError, match="can’t include the hotkey"):
        model.save()
    assert not path.exists()

    model.record_key(("right_ctrl",))
    assert model.problem is None
    model.save()
    assert (load_config(path).hotkey, load_config(path).hotkey_fallback) == ("right_ctrl", "ctrl+space")


def test_function_keys_and_linux_tap_keys_warn(model):
    model.record_key(("f5",))
    assert "F1 to F12" in model.warning
    assert model.status(None, None) == (model.warning, "warning")
    assert model.status("key", None) == (KEY_HINT, "hint")  # while recording, the hint comes first
    assert model.status("combination", TYPING_KEY) == (TYPING_KEY, "error")
    model.record_key(("f13",))
    assert model.warning is None
    assert model.status(None, None) == ("", None)

    model.record_key(("cmd",))
    assert (model.warning is not None and "Super" in model.warning) == (not MAC)
    model.record_key(("right_alt",))
    assert (model.warning is not None and "Alt" in model.warning) == (not MAC)
    model.record_key(("cmd_r",))
    assert model.warning is None


def test_fn_warns_that_macos_acts_on_it_too_and_pause_and_scroll_lock_do_not(model):
    if MAC:
        model.record_key(("fn",))
        assert "“Do Nothing”" in model.warning and "Dictation" in model.warning
        assert "“Press 🌐 key to”" in model.warning and "“Press fn key to”" in model.warning  # Globe and fn keyboards
        assert model.status(None, None) == (model.warning, "warning")
    else:
        for name in ("pause", "scroll_lock"):
            model.record_key((name,))
            assert model.key == name and model.warning is None


# -- Reading and saving ---------------------------------------------------------------


def test_reload_shows_the_saved_hotkey_in_any_spelling(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nkey = "Right Shift"\nfallback = "Left_Ctrl+Space"\n')
    model = loaded(path)
    assert label(model.key) == "Right Shift"
    assert combination_label(model.combination) == f"{label('ctrl')} + Space"
    assert combination_label("") == "None"
    assert not model.changed
    assert not model.is_default  # it has a combination


def test_saving_writes_the_hotkey_section_and_keeps_the_rest(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    model = loaded(path)
    model.record_key(("cmd_r",))
    model.save()
    text = path.read_text()
    assert text.startswith("# my settings")
    assert "sample_rate = 16000  # keep this comment" in text
    config = load_config(path)
    assert (config.hotkey, config.hotkey_fallback, config.double_tap_timeout_ms) == ("cmd_r", "Left_Ctrl+Space", 300)

    model.clear_combination()
    model.save()
    assert "fallback" not in tomllib.loads(path.read_text())["hotkey"]
    assert load_config(path).hotkey_fallback == ""


def test_saving_without_a_change_writes_nothing(tmp_path):
    path = tmp_path / "vox" / "config.toml"
    model = loaded(path)
    model.use_default()
    model.record_key(("right_shift",))
    model.save()
    assert not path.exists()


def test_use_default_goes_back_to_right_shift_without_a_combination(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nkey = "cmd_r"\nfallback = "ctrl+space"\n')
    model = loaded(path)
    assert not model.is_default
    model.use_default()
    assert (model.key, model.combination) == (DEFAULT_KEY, "") == ("right_shift", "")
    assert model.is_default
    model.save()
    config = load_config(path)
    assert (config.hotkey, config.hotkey_fallback) == ("right_shift", "")


@pytest.mark.parametrize("text", ["[hotkey\n", "[hotkey]\nkey = 5\n"])
def test_a_broken_settings_file_is_never_written(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    model = loaded(path)
    assert model.load_error
    model.record_key(("cmd_r",))
    with pytest.raises(ConfigError):
        model.save()
    assert path.read_text() == text
    assert model.unreadable.startswith("Couldn’t read ")
