"""Tests for the hotkey window: the shared model, then the macOS and Linux views built on it."""

import sys
import tomllib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vox import hotkey
from vox.config import load_config
from vox.errors import ConfigError
from vox.hotkey import HotkeyListener, parse_combo, resolve_key
from vox.ui.hotkey_model import (
    BAD_COMBINATION,
    COMBINATION_HINT,
    DEFAULT_KEY,
    KEY_HINT,
    LINUX_KEYS,
    LOAD_FAILED_TITLE,
    MAC,
    MAC_KEYS,
    MODIFIERS,
    NOT_ALONE,
    PRESS_KEY,
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
        key = next(k for k in keyboard.Key if k.value.vk == code and not getattr(k.value, "_is_media", False))
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


def test_main_leaves_reading_the_settings_to_the_window(tmp_path):
    """On Linux a second launch only forwards to the open window, so it must not read the file."""
    from vox.ui import hotkey_window

    path = tmp_path / "config.toml"
    module = "vox.ui.mac.hotkey" if sys.platform == "darwin" else "vox.ui.gtk.hotkey"
    fake = SimpleNamespace(run=MagicMock())
    with patch.dict(sys.modules, {module: fake}), patch("vox.ui.hotkey_model.load_config") as read:
        hotkey_window.main(["--config", str(path)])
    (model,), _ = fake.run.call_args
    assert isinstance(model, HotkeyModel) and model.path == path
    read.assert_not_called()


# -- macOS --------------------------------------------------------------------------


@pytest.fixture
def mac_window(appkit, tmp_path):
    """Build (never show) macOS windows on tmp_path/config.toml, written first when given text."""
    from vox.ui.mac.hotkey import HotkeyController

    made = []

    def build(text=None):
        path = tmp_path / "config.toml"
        if text is not None:
            path.write_text(text)
        controller = HotkeyController.alloc().initWithModel_(HotkeyModel(path))
        made.append(controller)
        return controller

    yield build
    for controller in made:
        controller.window.close()


# The device-independent flag each modifier key sets, next to its own device bit
_FLAGS = {
    56: "Shift", 60: "Shift", 59: "Control", 62: "Control", 58: "Option", 61: "Option", 55: "Command", 54: "Command",
}


def _key(AppKit, controller, code, repeat=False):
    return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        AppKit.NSEventTypeKeyDown, (0, 0), 0, 0, controller.window.windowNumber(), None, "", "", repeat, code
    )


def _flags(AppKit, controller, code, down):
    from vox.ui.mac.hotkey import _DOWN

    flags = 0
    if down and code in _DOWN:
        flags = _DOWN[code] | getattr(AppKit, f"NSEventModifierFlag{_FLAGS[code]}")
    elif down:  # fn
        flags = AppKit.NSEventModifierFlagFunction
    return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        AppKit.NSEventTypeFlagsChanged, (0, 0), flags, 0, controller.window.windowNumber(), None, "", "", False, code
    )


def _tap(AppKit, controller, code):
    assert controller.handle_key(_flags(AppKit, controller, code, True)) is None
    assert controller.handle_key(_flags(AppKit, controller, code, False)) is None


def test_mac_records_a_modifier_tapped_on_its_own(appkit, mac_window):
    window = mac_window()
    assert window.key_field.title() == "Right Shift"
    window.recordKey_(None)
    assert window.key_field.state() == appkit.NSControlStateValueOn
    assert window.key_field.title() == PRESS_KEY
    assert window.status.stringValue() == KEY_HINT

    _tap(appkit, window, 54)
    assert window.model.key == "cmd_r"
    assert window.key_field.title() == "Right Command"
    assert window.key_field.state() == appkit.NSControlStateValueOff
    assert window.status.isHidden()


def test_mac_records_a_combination_and_offers_to_clear_it(appkit, mac_window):
    window = mac_window()
    assert window.combination_field.title() == "None"
    assert window.clear_button.isHidden()
    window.recordCombination_(None)
    assert window.status.stringValue() == COMBINATION_HINT
    assert window.handle_key(_flags(appkit, window, 59, True)) is None
    assert window.combination_field.title() == "Left Control + …"
    assert window.handle_key(_key(appkit, window, 49)) is None  # Space
    assert window.model.combination == "ctrl+space"
    assert window.combination_field.title() == "Left Control + Space"
    assert not window.clear_button.isHidden()

    window.clearCombination_(None)
    assert window.model.combination == ""
    assert window.combination_field.title() == "None"
    assert window.clear_button.isHidden()


def test_mac_refuses_a_typing_key_and_keeps_recording(appkit, mac_window):
    window = mac_window()
    window.recordKey_(None)
    assert window.handle_key(_key(appkit, window, 0)) is None  # A: swallowed, never typed
    assert window.status.stringValue() == TYPING_KEY
    assert window.status.textColor() == appkit.NSColor.systemRedColor()
    assert window.recording == "key"
    assert window.model.key == "right_shift"

    _tap(appkit, window, 61)  # the next try works at once
    assert window.model.key == "right_alt"
    assert window.recording is None


def test_mac_escape_stops_recording_and_keeps_the_key(appkit, mac_window):
    window = mac_window()
    window.recordKey_(None)
    assert window.handle_key(_key(appkit, window, 53)) is None
    assert window.recording is None
    assert window.model.key == "right_shift"
    assert window.key_field.title() == "Right Shift"
    assert not window.closed

    window.recordKey_(None)
    window.recordKey_(None)  # clicking the field again stops too
    assert window.recording is None
    window.recordKey_(None)
    window.recordCombination_(None)  # and clicking the other one moves there
    assert window.recording == "combination"
    assert window.key_field.state() == appkit.NSControlStateValueOff


def test_mac_ignores_fn_so_function_keys_can_be_recorded_with_it(appkit, mac_window):
    window = mac_window()
    window.recordKey_(None)
    assert window.handle_key(_flags(appkit, window, 63, True)) is None
    assert window.recording == "key" and window.refusal is None
    assert window.handle_key(_key(appkit, window, 96)) is None  # F5
    assert window.model.key == "f5"


def test_mac_keys_pass_through_while_not_recording(appkit, mac_window):
    window = mac_window()
    for event in (_key(appkit, window, 36), _key(appkit, window, 53), _flags(appkit, window, 60, True)):
        assert window.handle_key(event) is event  # Return saves and Esc cancels through the buttons
    # A key still held after it was recorded repeats, and must not go on to press a button
    assert window.handle_key(_key(appkit, window, 49, repeat=True)) is None

    other = appkit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        appkit.NSMakeRect(0, 0, 100, 100), appkit.NSWindowStyleMaskTitled, appkit.NSBackingStoreBuffered, False
    )
    window.recordKey_(None)
    event = appkit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        appkit.NSEventTypeKeyDown, (0, 0), 0, 0, other.windowNumber(), None, "a", "a", False, 0
    )
    assert window.handle_key(event) is event
    other.close()


def test_mac_save_writes_the_settings_and_closes(appkit, tmp_path, mac_window):
    window = mac_window(CONFIG)
    assert window.combination_field.title() == "Left Control + Space"
    window.recordKey_(None)
    _tap(appkit, window, 54)
    window.recordCombination_(None)
    window.handle_key(_flags(appkit, window, 58, True))
    window.handle_key(_key(appkit, window, 105))  # F13
    window.save_(None)
    config = load_config(tmp_path / "config.toml")
    assert (config.hotkey, config.hotkey_fallback) == ("cmd_r", "alt+f13")
    assert window.closed


def test_mac_clash_keeps_the_window_open(appkit, tmp_path, mac_window):
    window = mac_window()
    window.recordKey_(None)
    _tap(appkit, window, 59)
    window.recordCombination_(None)
    window.handle_key(_flags(appkit, window, 59, True))
    window.handle_key(_key(appkit, window, 49))
    window.save_(None)
    assert not window.closed
    assert window.status.stringValue() == "The key combination can’t include the hotkey, Left Control."
    assert window.status.textColor() == appkit.NSColor.systemRedColor()
    assert not (tmp_path / "config.toml").exists()


def test_mac_broken_settings_file_disables_recording(appkit, mac_window):
    with patch("vox.ui.mac.kit.alert") as alert:
        window = mac_window("[hotkey\n")
    alert.assert_called_once()
    assert alert.call_args.args[1] == LOAD_FAILED_TITLE
    for control in (window.key_field, window.combination_field, window.default_button, window.save_button):
        assert not control.isEnabled()
    assert window.status.stringValue() == window.model.unreadable


def test_mac_warns_about_f1_to_f12(appkit, mac_window):
    window = mac_window()
    assert not window.default_button.isEnabled()  # already the default
    window.recordKey_(None)
    window.handle_key(_key(appkit, window, 96))  # F5
    assert "fn" in window.status.stringValue()
    assert window.status.textColor() == appkit.NSColor.systemOrangeColor()
    assert window.default_button.isEnabled()
    window.recordKey_(None)
    window.handle_key(_key(appkit, window, 105))  # F13
    assert window.status.isHidden()

    window.useDefault_(None)
    assert window.key_field.title() == "Right Shift"
    assert not window.default_button.isEnabled()


# -- Linux --------------------------------------------------------------------------


def _drain(gtk):
    context = gtk.GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


@pytest.fixture
def gtk_window(gtk, tmp_path):
    """Build Linux windows on tmp_path/config.toml, written first when given text."""
    from vox.ui.gtk.hotkey import HotkeyWindow

    made = []

    def build(text=None):
        path = tmp_path / "config.toml"
        if text is not None:
            path.write_text(text)
        window = HotkeyWindow(HotkeyModel(path))
        made.append(window)
        return window

    yield build
    for window in made:
        window.destroy()
    _drain(gtk)


def press(window, keyval):
    """A key press, with the key value standing in for the hardware keycode."""
    return window.key_pressed(None, keyval, keyval, 0)


def release(window, keyval):
    window.key_released(None, keyval, keyval, 0)


def tap(window, keyval):
    assert press(window, keyval) is True
    release(window, keyval)


def test_gtk_records_a_modifier_tapped_on_its_own(gtk, gtk_window):
    window = gtk_window()
    assert window.key_field.get_label() == "Right Shift"
    window.key_field.emit("clicked")
    assert window.key_field.has_css_class("suggested-action")
    assert window.key_field.get_label() == PRESS_KEY
    assert window.status.get_label() == KEY_HINT and window.status.has_css_class("dim-label")

    tap(window, gtk.Gdk.KEY_Super_R)
    assert window.model.key == "cmd_r"
    assert window.key_field.get_label() == "Right Super"
    assert not window.key_field.has_css_class("suggested-action")
    assert not window.status.get_visible()


def test_gtk_records_altgr_as_the_listener_names_it(gtk, gtk_window):
    window = gtk_window()
    window.key_field.emit("clicked")
    tap(window, gtk.Gdk.KEY_ISO_Level3_Shift)
    assert window.model.key == "vk_65027"
    assert window.key_field.get_label() == "AltGr"


def test_gtk_records_a_combination_and_offers_to_clear_it(gtk, gtk_window):
    window = gtk_window()
    assert window.combination_field.get_label() == "None"
    assert not window.clear_button.get_visible()
    window.combination_field.emit("clicked")
    assert press(window, gtk.Gdk.KEY_Control_L) is True
    assert window.combination_field.get_label() == "Left Ctrl + …"
    assert press(window, gtk.Gdk.KEY_space) is True
    assert window.model.combination == "ctrl+space"
    assert window.combination_field.get_label() == "Left Ctrl + Space"
    assert window.clear_button.get_visible()

    window.clear_button.emit("clicked")
    assert window.model.combination == ""
    assert window.combination_field.get_label() == "None"


def test_gtk_refuses_a_typing_key_and_keeps_recording(gtk, gtk_window):
    window = gtk_window()
    window.key_field.emit("clicked")
    assert press(window, gtk.Gdk.KEY_a) is True  # swallowed, never typed
    assert window.status.get_label() == TYPING_KEY
    assert window.status.has_css_class("error")
    assert window.recording == "key"
    release(window, gtk.Gdk.KEY_a)

    tap(window, gtk.Gdk.KEY_Shift_L)
    assert window.model.key == "shift"
    assert window.recording is None


def test_gtk_escape_stops_recording_and_keeps_the_key(gtk, gtk_window):
    window = gtk_window()
    window.key_field.emit("clicked")
    assert press(window, gtk.Gdk.KEY_Escape) is True
    assert window.recording is None
    assert window.model.key == "right_shift"
    assert not window.closed
    release(window, gtk.Gdk.KEY_Escape)
    assert press(window, gtk.Gdk.KEY_Escape) is False  # not recording: the window's Escape shortcut cancels


def test_gtk_swallows_a_held_key_after_recording_it(gtk, gtk_window):
    window = gtk_window()
    window.key_field.emit("clicked")
    assert press(window, gtk.Gdk.KEY_F13) is True
    assert window.model.key == "f13" and window.recording is None
    assert press(window, gtk.Gdk.KEY_F13) is True  # the key repeats while held: it must not press a button
    release(window, gtk.Gdk.KEY_F13)
    assert press(window, gtk.Gdk.KEY_Return) is False  # other keys reach the window again


def test_gtk_save_writes_the_settings_and_closes(gtk, tmp_path, gtk_window):
    window = gtk_window(CONFIG)
    window.key_field.emit("clicked")
    tap(window, gtk.Gdk.KEY_Control_R)
    window.combination_field.emit("clicked")
    press(window, gtk.Gdk.KEY_Super_L)
    press(window, gtk.Gdk.KEY_F5)
    window.save()
    config = load_config(tmp_path / "config.toml")
    assert (config.hotkey, config.hotkey_fallback) == ("right_ctrl", "cmd+f5")
    assert "# keep this comment" in (tmp_path / "config.toml").read_text()
    assert window.closed


def test_gtk_broken_settings_file_shows_a_banner_and_disables_recording(gtk, gtk_window):
    with patch("vox.ui.gtk.hotkey.error_dialog") as dialog:
        window = gtk_window("[hotkey\n")
    dialog.assert_called_once()
    assert dialog.call_args.args[1] == LOAD_FAILED_TITLE
    assert window.banner.get_revealed()
    for widget in (window.key_field, window.combination_field, window.default_button, window.save_button):
        assert not widget.get_sensitive()
