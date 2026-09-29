"""Tests for the Settings window: its launch, then the macOS and Linux windows built on the models.

Every test runs on conftest's in-memory keyring with dummy keys, and never calls OpenAI.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import keyring
import pytest
from keyring.backends import fail

from vox import keystore
from vox.config import load_config
from vox.ui.hotkey_model import (
    BAD_COMBINATION,
    COMBINATION_HINT,
    KEY_HINT,
    PRESS_KEY,
    TYPING_KEY,
    HotkeyModel,
)
from vox.ui.key_model import BAD_CHARACTER, CheckResult, KeyModel, Outcome
from vox.ui.settings_model import DETECT_LANGUAGE, DICTATION_OFF, NOT_CONNECTED, PAGES, SYSTEM_DEFAULT, SettingsModel
from vox.ui.vocab_model import VocabModel

KEY = "sk-test-dummy-0001"
OTHER = "sk-test-dummy-0002"
DEVICES = [(0, "MacBook Pro Microphone"), (2, "USB Audio Interface")]

CONFIG = """\
# my settings
dictionary = ["FastAPI"]

[hotkey]
key = "right_shift"
fallback = "Left_Ctrl+Space"

[snippets]
"my email" = "alex@example.com"
"""


def stored(backend):
    return backend.items.get((keystore.SERVICE, keystore.USERNAME))


def local_setup(tmp_path):
    binary = tmp_path / "whisper-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    model = tmp_path / "ggml.bin"
    model.write_bytes(b"model")
    return binary, model


# -- Launch -------------------------------------------------------------------------


def test_main_leaves_reading_the_settings_to_the_window(tmp_path):
    """On Linux a second launch only forwards to the open window, so it must read nothing."""
    from vox.ui import settings_window

    path = tmp_path / "config.toml"
    module = "vox.ui.mac.settings" if sys.platform == "darwin" else "vox.ui.gtk.settings"
    fake = SimpleNamespace(run=MagicMock())
    with patch.dict(sys.modules, {module: fake}), \
         patch("vox.ui.settings_model.load_config") as read_settings, \
         patch("vox.ui.hotkey_model.load_config") as read_hotkey, \
         patch("vox.ui.vocab_model.load_config") as read_vocab, \
         patch.object(keystore, "get_stored_key") as read_key:
        settings_window.main(["--config", str(path), "--page", "hotkey"])
    (settings, hotkey, vocab, key), kwargs = fake.run.call_args
    assert kwargs == {"page": "hotkey"}
    assert isinstance(settings, SettingsModel) and settings.path == path
    assert isinstance(hotkey, HotkeyModel) and hotkey.path == path
    assert isinstance(vocab, VocabModel) and vocab.path == path
    assert isinstance(key, KeyModel)
    for read in (read_settings, read_hotkey, read_vocab, read_key):
        read.assert_not_called()


def test_main_opens_on_general_and_refuses_an_unknown_page(tmp_path):
    from vox.ui import settings_window

    module = "vox.ui.mac.settings" if sys.platform == "darwin" else "vox.ui.gtk.settings"
    fake = SimpleNamespace(run=MagicMock())
    with patch.dict(sys.modules, {module: fake}):
        settings_window.main(["--config", str(tmp_path / "config.toml")])
        assert fake.run.call_args.kwargs == {"page": "general"}
        with pytest.raises(SystemExit):
            settings_window.main(["--page", "advanced"])


# -- macOS --------------------------------------------------------------------------


@pytest.fixture
def mac_settings(appkit, tmp_path, memory_keyring):
    """Build (never show) Settings windows on tmp_path/config.toml, written first when given text.

    Work meant for a background thread runs inline, confirmations are answered yes, and
    Choose… picks whatever ``controller.chosen`` holds.
    """
    from vox.ui.mac.settings import SettingsController

    made = []
    devices = list(DEVICES)

    def build(text=None):
        path = tmp_path / "config.toml"
        if text is not None:
            path.write_text(text)
        settings = SettingsModel(path, devices=lambda channels: list(devices))
        controller = SettingsController.alloc().initWithSettings_hotkey_vocab_key_(
            settings, HotkeyModel(path), VocabModel(path), KeyModel()
        )
        controller.path = path
        controller.devices = devices
        controller.background = lambda work, done: done(work())
        controller.confirmed = []

        def confirm(window, title, message, button, destructive, then):
            controller.confirmed.append(title)
            then()

        controller.confirm = confirm
        controller.chosen = None
        controller.transcription.choose_file = lambda window, current, then: then(controller.chosen)
        made.append(controller)
        return controller

    yield build
    for controller in made:
        controller.close_editor()
        if controller.transcription.sheet is not None:
            controller.transcription.sheet.cancel_(None)
        controller.window.close()


def on(appkit, button):
    return button.state() == appkit.NSControlStateValueOn


def click(appkit, button, state=None):
    """Press a checkbox or radio button as a click would: set its state, then send its action."""
    if state is not None:
        button.setState_(appkit.NSControlStateValueOn if state else appkit.NSControlStateValueOff)
    button.target().performSelector_withObject_(button.action(), button)


def pick(popup, title):
    popup.selectItemWithTitle_(title)
    popup.target().performSelector_withObject_(popup.action(), popup)


def titles(popup):
    return [popup.itemAtIndex_(i).title() for i in range(popup.numberOfItems())]


def tab_identifiers(controller):
    return [item.identifier() for item in controller.tabs.tabViewItems()]


def selected_tab(controller):
    return controller.tabs.tabViewItems()[controller.tabs.selectedTabViewItemIndex()].identifier()


# 5.1: the window and its tabs


def test_mac_has_the_five_tabs_and_says_dictation_is_off(appkit, mac_settings):
    controller = mac_settings()
    assert tab_identifiers(controller) == list(PAGES)
    assert [item.label() for item in controller.tabs.tabViewItems()] == [
        "General", "Hotkey", "Transcription", "Vocabulary", "Snippets",
    ]
    assert selected_tab(controller) == "general"
    assert controller.window.title() == "Settings"
    assert controller.window.subtitle() == DICTATION_OFF


@pytest.mark.parametrize("page", PAGES)
def test_mac_run_opens_on_the_page_asked_for(appkit, tmp_path, memory_keyring, page):
    from vox.ui.mac import settings

    path = tmp_path / "config.toml"
    with patch.object(settings.kit, "run") as run_window:
        settings.run(SettingsModel(path, devices=lambda c: []), HotkeyModel(path), VocabModel(path), KeyModel(), page=page)
    (window,), _ = run_window.call_args
    tabs = window.contentViewController()
    assert tabs.tabViewItems()[tabs.selectedTabViewItemIndex()].identifier() == page
    window.close()


def test_mac_opening_and_closing_without_a_change_creates_no_file(appkit, tmp_path, mac_settings):
    controller = mac_settings()
    controller.window.close()
    assert not (tmp_path / "config.toml").exists()


# 5.2: General


def test_mac_microphone_popup_saves_the_device_by_name(appkit, mac_settings):
    controller = mac_settings()
    general = controller.general
    assert titles(general.microphone) == [SYSTEM_DEFAULT, "MacBook Pro Microphone", "USB Audio Interface"]
    assert general.microphone.titleOfSelectedItem() == SYSTEM_DEFAULT

    pick(general.microphone, "USB Audio Interface")
    assert load_config(controller.path).audio_device == "USB Audio Interface"
    pick(general.microphone, SYSTEM_DEFAULT)
    assert load_config(controller.path).audio_device is None


def test_mac_refresh_lists_a_new_microphone_and_keeps_a_missing_one(appkit, mac_settings):
    controller = mac_settings('[audio]\ndevice = "USB Audio Interface"\n')
    general = controller.general
    controller.devices[:] = [(0, "MacBook Pro Microphone"), (3, "AirPods")]
    general.refreshDevices_(None)
    assert titles(general.microphone) == [
        SYSTEM_DEFAULT, "MacBook Pro Microphone", "AirPods", f"USB Audio Interface {NOT_CONNECTED}",
    ]
    assert general.microphone.titleOfSelectedItem() == f"USB Audio Interface {NOT_CONNECTED}"
    assert general.refresh_button.isEnabled()
    assert load_config(controller.path).audio_device == "USB Audio Interface"


def test_mac_recording_limit_popup(appkit, mac_settings):
    controller = mac_settings()
    limit = controller.general.limit
    assert limit.titleOfSelectedItem() == "15 min"
    pick(limit, "30 min")
    assert load_config(controller.path).max_recording_seconds == 1800


def test_mac_checkboxes_save_at_once(appkit, mac_settings):
    controller = mac_settings()
    general = controller.general
    assert on(appkit, general.sounds) and on(appkit, general.lower) and on(appkit, general.screen)

    click(appkit, general.sounds, False)
    click(appkit, general.screen, False)
    click(appkit, general.lower, False)
    config = load_config(controller.path)
    assert (config.sounds_enabled, config.context_screen, config.attenuation_enabled) == (False, False, False)
    assert not general.level.isEnabled()  # the level only matters while other audio is lowered

    click(appkit, general.lower, True)
    assert general.level.isEnabled()


def test_mac_level_slider_saves_in_five_percent_steps(appkit, mac_settings):
    controller = mac_settings()
    general = controller.general
    general.level.setDoubleValue_(33)
    general.levelChanged_(general.level)  # no mouse event: a release, or the keyboard
    assert load_config(controller.path).attenuation_level == pytest.approx(0.35)
    assert general.level_text.stringValue() == "35%"


def test_mac_level_slider_saves_nothing_while_it_is_dragged(appkit, mac_settings):
    controller = mac_settings()
    general = controller.general
    drag = appkit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
        appkit.NSEventTypeLeftMouseDragged, (0, 0), 0, 0, controller.window.windowNumber(), None, 0, 1, 1.0
    )
    general.level.setDoubleValue_(70)
    with patch("vox.ui.mac.settings._current_event", return_value=drag):
        general.levelChanged_(general.level)
    assert general.level_text.stringValue() == "70%"
    assert not controller.path.exists()


# 5.3: Hotkey


# The device-independent flag each modifier key sets, next to its own device bit
_FLAGS = {
    56: "Shift", 60: "Shift", 59: "Control", 62: "Control", 58: "Option", 61: "Option", 55: "Command", 54: "Command",
    63: "Function",
}


def _key(AppKit, controller, code, repeat=False, flags=0, chars=""):
    return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        AppKit.NSEventTypeKeyDown, (0, 0), flags, 0, controller.window.windowNumber(), None, chars, chars, repeat, code
    )


def _key_up(AppKit, controller, code):
    return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        AppKit.NSEventTypeKeyUp, (0, 0), 0, 0, controller.window.windowNumber(), None, "", "", False, code
    )


def _flags(AppKit, controller, code, down):
    from vox.ui.mac.hotkey import _DOWN

    flags = _DOWN[code] | getattr(AppKit, f"NSEventModifierFlag{_FLAGS[code]}") if down else 0
    return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        AppKit.NSEventTypeFlagsChanged, (0, 0), flags, 0, controller.window.windowNumber(), None, "", "", False, code
    )


def _tap(AppKit, controller, code):
    assert controller.handle_key(_flags(AppKit, controller, code, True)) is None
    assert controller.handle_key(_flags(AppKit, controller, code, False)) is None


def test_mac_records_a_modifier_tapped_on_its_own_and_saves_it(appkit, mac_settings):
    controller = mac_settings()
    page = controller.hotkey
    assert page.key_field.title() == "Right Shift"
    page.recordKey_(None)
    assert page.key_field.state() == appkit.NSControlStateValueOn
    assert page.key_field.title() == PRESS_KEY
    assert page.status.stringValue() == KEY_HINT

    _tap(appkit, controller, 54)
    assert load_config(controller.path).hotkey == "cmd_r"
    assert page.key_field.title() == "Right Command"
    assert page.key_field.state() == appkit.NSControlStateValueOff
    assert page.status.isHidden()


def test_mac_records_a_combination_and_clears_it(appkit, mac_settings):
    controller = mac_settings()
    page = controller.hotkey
    assert page.combination_field.title() == "None"
    assert page.clear_button.isHidden()
    page.recordCombination_(None)
    assert page.status.stringValue() == COMBINATION_HINT
    assert controller.handle_key(_flags(appkit, controller, 59, True)) is None
    assert page.combination_field.title() == "Left Control + …"
    assert controller.handle_key(_key(appkit, controller, 49)) is None  # Space
    assert load_config(controller.path).hotkey_fallback == "ctrl+space"
    assert page.combination_field.title() == "Left Control + Space"
    assert not page.clear_button.isHidden()

    page.clearCombination_(None)
    assert load_config(controller.path).hotkey_fallback == ""
    assert page.combination_field.title() == "None"
    assert page.clear_button.isHidden()


def test_mac_refuses_a_typing_key_and_keeps_recording(appkit, mac_settings):
    controller = mac_settings()
    page = controller.hotkey
    page.recordKey_(None)
    assert controller.handle_key(_key(appkit, controller, 0)) is None  # A: swallowed, never typed
    assert page.status.stringValue() == TYPING_KEY
    assert page.status.textColor() == appkit.NSColor.systemRedColor()
    assert page.recording == "key"
    assert not controller.path.exists()

    _tap(appkit, controller, 61)  # the next try works at once
    assert load_config(controller.path).hotkey == "right_alt"
    assert page.recording is None


def test_mac_takes_every_key_while_recording(appkit, mac_settings):
    """Cmd+W, Esc and Cmd+N would close the window or open the snippet editor if they got through."""
    controller = mac_settings()
    page = controller.hotkey
    page.recordKey_(None)
    command = appkit.NSEventModifierFlagCommand
    assert controller.handle_key(_key(appkit, controller, 13, flags=command, chars="w")) is None
    assert controller.handle_key(_key(appkit, controller, 45, flags=command, chars="n")) is None
    assert controller.editor is None
    assert controller.handle_key(_key_up(appkit, controller, 45)) is None
    assert page.recording == "key"

    assert controller.handle_key(_key(appkit, controller, 53)) is None  # Esc stops recording, and only that
    assert page.recording is None
    assert page.key_field.title() == "Right Shift"
    assert controller.window.delegate() is controller
    assert not controller.path.exists()


def test_mac_clicking_the_fields_starts_stops_and_moves_recording(appkit, mac_settings):
    page = mac_settings().hotkey
    page.recordKey_(None)
    page.recordKey_(None)  # clicking the field again stops
    assert page.recording is None
    page.recordKey_(None)
    page.recordCombination_(None)  # and clicking the other one moves there
    assert page.recording == "combination"
    assert page.key_field.state() == appkit.NSControlStateValueOff


def test_mac_records_fn_tapped_on_its_own_and_warns(appkit, mac_settings):
    controller = mac_settings()
    page = controller.hotkey
    page.recordKey_(None)
    _tap(appkit, controller, 63)
    assert load_config(controller.path).hotkey == "fn"
    assert page.key_field.title() == "fn (Globe)"
    assert "“Do Nothing”" in page.status.stringValue()
    assert page.status.textColor() == appkit.NSColor.systemOrangeColor()


def test_mac_records_the_key_pressed_while_fn_is_held(appkit, mac_settings):
    controller = mac_settings()
    page = controller.hotkey
    page.recordKey_(None)
    assert controller.handle_key(_flags(appkit, controller, 63, True)) is None
    assert page.recording == "key" and page.refusal is None
    assert controller.handle_key(_key(appkit, controller, 96)) is None  # F5
    assert page.model.key == "f5"
    assert page.recording is None

    page.recordKey_(None)
    controller.handle_key(_flags(appkit, controller, 63, True))
    _tap(appkit, controller, 54)
    assert page.model.key == "cmd_r"
    controller.handle_key(_flags(appkit, controller, 63, False))
    assert load_config(controller.path).hotkey == "cmd_r"


def test_mac_keeps_fn_out_of_combinations(appkit, mac_settings):
    controller = mac_settings()
    page = controller.hotkey
    page.recordCombination_(None)
    _tap(appkit, controller, 63)
    assert page.refusal == BAD_COMBINATION
    assert page.recording == "combination"

    controller.handle_key(_flags(appkit, controller, 59, True))
    controller.handle_key(_flags(appkit, controller, 63, True))
    assert page.combination_field.title() == "Left Control + …"
    controller.handle_key(_key(appkit, controller, 96))  # F5
    assert load_config(controller.path).hotkey_fallback == "ctrl+f5"


def test_mac_keys_reach_the_window_while_not_recording(appkit, mac_settings):
    controller = mac_settings()
    for event in (_key(appkit, controller, 0, chars="a"), _key(appkit, controller, 36), _flags(appkit, controller, 60, True),
                  _key_up(appkit, controller, 0), _key(appkit, controller, 0, repeat=True, chars="a")):
        assert controller.handle_key(event) is event  # typing, and holding a key down, in the other tabs' fields

    # A key still held after it completed a recording repeats, and must not go on to press a button
    controller.hotkey.recordKey_(None)
    controller.handle_key(_key(appkit, controller, 96))  # F5
    assert controller.handle_key(_key(appkit, controller, 96, repeat=True)) is None
    assert controller.handle_key(_key_up(appkit, controller, 96)) is None
    held_a = _key(appkit, controller, 0, repeat=True, chars="a")
    assert controller.handle_key(held_a) is held_a

    other = appkit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        appkit.NSMakeRect(0, 0, 100, 100), appkit.NSWindowStyleMaskTitled, appkit.NSBackingStoreBuffered, False
    )
    controller.hotkey.recordKey_(None)
    event = appkit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        appkit.NSEventTypeKeyDown, (0, 0), 0, 0, other.windowNumber(), None, "a", "a", False, 0
    )
    assert controller.handle_key(event) is event
    other.close()


def test_mac_saves_each_recording_and_keeps_the_rest_of_the_file(appkit, mac_settings):
    controller = mac_settings(CONFIG)
    page = controller.hotkey
    assert page.combination_field.title() == "Left Control + Space"
    page.recordKey_(None)
    _tap(appkit, controller, 54)
    assert load_config(controller.path).hotkey == "cmd_r"
    page.recordCombination_(None)
    controller.handle_key(_flags(appkit, controller, 58, True))
    controller.handle_key(_key(appkit, controller, 105))  # F13
    config = load_config(controller.path)
    assert (config.hotkey, config.hotkey_fallback) == ("cmd_r", "alt+f13")
    assert config.snippets == {"my email": "alex@example.com"}
    assert controller.path.read_text().startswith("# my settings\n")


def test_mac_a_clash_is_refused_and_nothing_is_saved(appkit, mac_settings):
    controller = mac_settings()
    page = controller.hotkey
    page.recordKey_(None)
    _tap(appkit, controller, 59)
    before = controller.path.read_text()
    page.recordCombination_(None)
    controller.handle_key(_flags(appkit, controller, 59, True))
    controller.handle_key(_key(appkit, controller, 49))
    assert page.status.stringValue() == "The key combination can’t include the hotkey, Left Control."
    assert page.status.textColor() == appkit.NSColor.systemRedColor()
    assert page.combination_field.title() == "None"
    assert controller.path.read_text() == before

    page.recordCombination_(None)  # trying again clears the refusal
    assert page.status.stringValue() == COMBINATION_HINT


def test_mac_switching_tabs_or_windows_stops_recording(appkit, mac_settings):
    controller = mac_settings()
    controller.select_tab("hotkey")
    controller.hotkey.recordKey_(None)
    controller.select_tab("general")
    assert controller.hotkey.recording is None
    assert selected_tab(controller) == "general"

    controller.select_tab("hotkey")
    controller.hotkey.recordCombination_(None)
    controller.windowDidResignKey_(None)
    assert controller.hotkey.recording is None
    assert not controller.path.exists()


def test_mac_warns_about_f1_to_f12_and_use_default_goes_back(appkit, mac_settings):
    controller = mac_settings()
    page = controller.hotkey
    assert not page.default_button.isEnabled()  # already the default
    page.recordKey_(None)
    controller.handle_key(_key(appkit, controller, 96))  # F5
    assert "fn" in page.status.stringValue()
    assert page.status.textColor() == appkit.NSColor.systemOrangeColor()
    assert page.default_button.isEnabled()
    controller.handle_key(_key_up(appkit, controller, 96))
    page.recordKey_(None)
    controller.handle_key(_key(appkit, controller, 105))  # F13
    assert page.status.isHidden()

    page.useDefault_(None)
    assert page.key_field.title() == "Right Shift"
    assert not page.default_button.isEnabled()
    assert load_config(controller.path).hotkey == "right_shift"


# 5.4: Transcription


def mode_buttons(controller):
    return controller.transcription.mode_buttons


def test_mac_without_a_key_the_openai_modes_say_why(appkit, mac_settings):
    controller = mac_settings()
    page = controller.transcription
    buttons, reasons = page.mode_buttons, page.mode_reasons
    assert on(appkit, buttons["batch"]) and buttons["batch"].isEnabled()  # the saved mode stays selectable
    assert not buttons["streaming"].isEnabled()
    assert reasons["streaming"].stringValue() == "Set an OpenAI API key before selecting OpenAI transcription"
    assert reasons["batch"].textColor() == appkit.NSColor.systemRedColor()  # the saved mode can't run
    assert not buttons["whisper_cpp"].isEnabled()
    assert not reasons["whisper_cpp"].isHidden()
    assert page.key_button.title() == "Set Key…"
    assert page.remove_key_button.isHidden()


def open_sheet(appkit, controller, check=False):
    controller.transcription.setKey_(None)
    sheet = controller.transcription.sheet
    assert sheet is not None
    sheet.background = lambda work, done: done(work())
    sheet.confirmed = []

    def confirm(window, title, message, button, destructive, then):
        sheet.confirmed.append(title)
        then()

    sheet.confirm = confirm
    sheet.check_box.setState_(appkit.NSControlStateValueOn if check else appkit.NSControlStateValueOff)
    return sheet


def test_mac_a_key_saved_in_the_sheet_enables_the_openai_modes(appkit, memory_keyring, mac_settings):
    controller = mac_settings()
    page = controller.transcription
    sheet = open_sheet(appkit, controller)
    sheet.secure_field.setStringValue_(f" {KEY} ")
    sheet.save_(None)
    assert stored(memory_keyring) == KEY
    assert page.sheet is None and sheet.closed
    assert page.mode_buttons["streaming"].isEnabled()
    assert page.mode_reasons["streaming"].isHidden()
    assert page.key_text.stringValue() == "A key ending in 0001 is saved."
    assert page.key_button.title() == "Replace Key…"
    assert not page.remove_key_button.isHidden()

    click(appkit, page.mode_buttons["streaming"], True)
    assert load_config(controller.path).mode == "streaming"
    assert on(appkit, page.mode_buttons["streaming"]) and not on(appkit, page.mode_buttons["batch"])


def test_mac_the_sheet_checks_with_openai_by_default(appkit, mac_settings):
    controller = mac_settings()
    controller.transcription.setKey_(None)
    assert controller.transcription.sheet.check_box.state() == appkit.NSControlStateValueOn
    controller.transcription.setKey_(None)  # a second click keeps the one sheet


def test_mac_cancel_closes_the_sheet_and_saves_nothing(appkit, memory_keyring, mac_settings):
    controller = mac_settings()
    sheet = open_sheet(appkit, controller)
    sheet.secure_field.setStringValue_(KEY)
    sheet.cancel_(None)
    assert controller.transcription.sheet is None
    assert stored(memory_keyring) is None


def test_mac_blank_key_is_refused(appkit, memory_keyring, mac_settings):
    controller = mac_settings()
    sheet = open_sheet(appkit, controller)
    sheet.save_(None)
    assert sheet.status.stringValue() == "Paste your OpenAI API key."
    assert stored(memory_keyring) is None
    assert controller.transcription.sheet is sheet


def test_mac_key_with_an_invisible_character_is_refused(appkit, memory_keyring, mac_settings):
    controller = mac_settings()
    sheet = open_sheet(appkit, controller)
    sheet.secure_field.setStringValue_("sk-test-dummy​0001")
    sheet.save_(None)
    assert sheet.status.stringValue() == BAD_CHARACTER
    assert stored(memory_keyring) is None
    assert controller.transcription.sheet is sheet


def test_mac_accepted_key_is_saved(appkit, memory_keyring, mac_settings):
    controller = mac_settings()
    sheet = open_sheet(appkit, controller, check=True)
    sheet.secure_field.setStringValue_(KEY)
    with patch("vox.ui.mac.key.check_key", return_value=CheckResult(Outcome.ACCEPTED)) as check:
        sheet.save_(None)
    check.assert_called_once_with(KEY)
    assert stored(memory_keyring) == KEY
    assert controller.transcription.sheet is None


def test_mac_rejected_key_is_not_saved(appkit, memory_keyring, mac_settings):
    controller = mac_settings()
    sheet = open_sheet(appkit, controller, check=True)
    sheet.secure_field.setStringValue_(KEY)
    with patch("vox.ui.mac.key.check_key", return_value=CheckResult(Outcome.REJECTED, "OpenAI didn’t accept this key.")):
        sheet.save_(None)
    assert stored(memory_keyring) is None
    assert sheet.status.stringValue() == "OpenAI didn’t accept this key."
    assert sheet.save_button.isEnabled()
    assert controller.transcription.sheet is sheet


def test_mac_unchecked_key_can_be_saved_anyway(appkit, memory_keyring, mac_settings):
    controller = mac_settings()
    sheet = open_sheet(appkit, controller, check=True)
    sheet.secure_field.setStringValue_(KEY)
    with patch("vox.ui.mac.key.check_key", return_value=CheckResult(Outcome.UNCHECKED, "Vox Transfer couldn’t reach OpenAI.")):
        sheet.save_(None)
    assert sheet.confirmed == ["Couldn’t Check the Key"]
    assert stored(memory_keyring) == KEY


def test_mac_show_swaps_in_a_plain_field(appkit, memory_keyring, mac_settings):
    controller = mac_settings()
    sheet = open_sheet(appkit, controller)
    sheet.secure_field.setStringValue_(KEY)
    sheet.show_box.setState_(appkit.NSControlStateValueOn)
    sheet.toggleShow_(None)
    assert sheet.secure_field.isHidden() and not sheet.plain_field.isHidden()
    assert sheet.plain_field.stringValue() == KEY
    sheet.plain_field.setStringValue_(OTHER)
    sheet.save_(None)
    assert stored(memory_keyring) == OTHER


def test_mac_remove_is_offered_only_for_a_saved_key(appkit, memory_keyring, mac_settings):
    keystore.set_api_key(KEY)
    controller = mac_settings('[transcription]\nmode = "streaming"\n')
    page = controller.transcription
    assert not page.remove_key_button.isHidden()
    page.removeKey_(None)
    assert controller.confirmed == ["Remove the Saved Key?"]
    assert stored(memory_keyring) is None
    assert page.remove_key_button.isHidden()
    assert page.key_button.title() == "Set Key…"
    assert not page.mode_buttons["batch"].isEnabled()
    assert page.mode_buttons["streaming"].isEnabled()  # still the saved mode, now with its reason in red
    assert page.mode_reasons["streaming"].textColor() == appkit.NSColor.systemRedColor()


def test_mac_keychain_error_keeps_the_sheet_open(appkit, memory_keyring, mac_settings):
    controller = mac_settings()
    sheet = open_sheet(appkit, controller)
    sheet.secure_field.setStringValue_(KEY)
    with patch.object(KeyModel, "save", side_effect=keystore.KeystoreError("User interaction is not allowed.")), \
         patch("vox.ui.mac.kit.alert") as alert:
        sheet.save_(None)
    alert.assert_called_once()
    assert alert.call_args.args[1] == "Couldn’t Save the Key"
    assert controller.transcription.sheet is sheet


def test_mac_warns_when_the_key_would_not_be_encrypted(appkit, mac_settings):
    keyring.set_keyring(fail.Keyring())
    controller = mac_settings()
    controller.transcription.setKey_(None)
    assert "without encryption" in controller.transcription.sheet.storage.stringValue()


def test_mac_language_popup(appkit, mac_settings):
    controller = mac_settings()
    language = controller.transcription.language
    assert language.titleOfSelectedItem() == DETECT_LANGUAGE
    pick(language, "German")
    assert load_config(controller.path).whisper_language == "de"
    pick(language, DETECT_LANGUAGE)
    assert load_config(controller.path).whisper_language is None


def test_mac_prompt_is_saved_on_commit_and_when_the_window_closes(appkit, mac_settings):
    controller = mac_settings()
    prompt = controller.transcription.prompt
    prompt.setStringValue_("Vox Transfer, FastAPI.")
    controller.transcription.promptChanged_(prompt)
    assert load_config(controller.path).whisper_prompt == "Vox Transfer, FastAPI."

    prompt.setStringValue_("Kubernetes.")
    controller.window.close()
    assert load_config(controller.path).whisper_prompt == "Kubernetes."


def test_mac_choosing_the_whisper_cpp_files_enables_local_mode(appkit, tmp_path, mac_settings):
    binary, ggml = local_setup(tmp_path)
    controller = mac_settings()
    page = controller.transcription
    assert page.model_text.stringValue() == "None chosen"

    controller.chosen = str(binary)
    page.chooseBinary_(None)
    controller.chosen = str(ggml)
    page.chooseModel_(None)
    config = load_config(controller.path)
    assert (config.whisper_cpp_binary, config.whisper_cpp_model) == (str(binary), str(ggml))
    assert page.binary_text.stringValue() == str(binary)
    assert page.mode_buttons["whisper_cpp"].isEnabled()
    assert page.mode_reasons["whisper_cpp"].isHidden()

    click(appkit, page.mode_buttons["whisper_cpp"], True)
    assert load_config(controller.path).mode == "whisper_cpp"


def test_mac_a_save_that_fails_is_explained_and_the_page_shows_the_file(appkit, mac_settings):
    controller = mac_settings()
    with patch("vox.ui.settings_model.update_flag", side_effect=OSError("disk full")), \
         patch("vox.ui.mac.kit.alert") as alert:
        click(appkit, controller.general.sounds, False)
    assert alert.call_args.args[1:] == ("Couldn’t Save", "disk full")
    assert on(appkit, controller.general.sounds)


# Vocabulary and Snippets, as in the vocabulary window


def test_mac_lists_words_and_snippets_and_adds_words(appkit, mac_settings):
    controller = mac_settings(CONFIG)
    assert controller.words_table.numberOfRows() == 1
    assert controller.snippets_table.numberOfRows() == 1
    controller.word_field.setStringValue_("Kubernetes, PostgreSQL")
    controller.addWord_(None)
    assert load_config(controller.path).dictionary == ["FastAPI", "Kubernetes", "PostgreSQL"]
    assert controller.word_field.stringValue() == ""


def test_mac_snippet_editor_adds_a_multiline_snippet(appkit, mac_settings):
    controller = mac_settings(CONFIG)
    controller.select_tab("snippets")
    command = appkit.NSEventModifierFlagCommand
    assert controller.handle_key(_key(appkit, controller, 45, flags=command, chars="т")) is None  # N, on a Cyrillic layout
    editor = controller.editor
    editor.trigger.setStringValue_("sign off")
    editor.expansion.setString_("Best,\nAlex")
    editor.validate()
    editor.save_(None)
    assert load_config(controller.path).snippets["sign off"] == "Best,\nAlex"
    assert controller.editor is None


def test_mac_delete_key_removes_the_selected_word(appkit, mac_settings):
    controller = mac_settings(CONFIG)
    controller.select_tab("vocabulary")
    table = controller.words_table
    controller.window.makeFirstResponder_(table)
    table.selectRowIndexes_byExtendingSelection_(appkit.NSIndexSet.indexSetWithIndex_(0), False)
    assert controller.handle_key(_key(appkit, controller, 51, chars="\x7f")) is None
    assert load_config(controller.path).dictionary == []


# 5.5: a file that can't be read, and edits made by hand


def test_mac_a_broken_file_disables_the_settings_but_not_the_key(appkit, memory_keyring, mac_settings):
    with patch("vox.ui.mac.kit.alert") as alert:
        controller = mac_settings("[audio\n")
    alert.assert_called_once()
    general, page, hotkey = controller.general, controller.transcription, controller.hotkey
    for control in (general.microphone, general.limit, general.sounds, general.screen, general.level,
                    page.language, page.prompt, page.binary_button, *page.mode_buttons.values(),
                    hotkey.key_field, hotkey.combination_field, controller.word_field, controller.new_button):
        assert not control.isEnabled()
    for footer in (general.footer, page.footer):
        assert footer.stringValue().startswith("Couldn’t read ")
        assert footer.textColor() == appkit.NSColor.systemRedColor()
    assert hotkey.status.textColor() == appkit.NSColor.systemRedColor()

    assert page.key_button.isEnabled()
    sheet = open_sheet(appkit, controller)
    sheet.secure_field.setStringValue_(KEY)
    sheet.save_(None)
    assert stored(memory_keyring) == KEY
    assert controller.path.read_text() == "[audio\n"


def test_mac_breaking_and_fixing_the_file_while_open(appkit, mac_settings):
    controller = mac_settings("[audio]\nmax_recording_seconds = 120\n")
    general = controller.general
    assert not controller.settings.poll()  # nothing changed, nothing read

    controller.path.write_text("[audio\nmax_recording_seconds = 60\n")
    with patch("vox.ui.mac.kit.alert") as alert:
        controller.poll()
    alert.assert_not_called()  # the red lines say it, without a dialog every 2 seconds
    assert not general.sounds.isEnabled()
    assert not controller.hotkey.key_field.isEnabled()
    assert not controller.word_field.isEnabled()
    assert general.footer.textColor() == appkit.NSColor.systemRedColor()
    assert general.limit.titleOfSelectedItem() == "2 min"  # the last settings read stay shown

    controller.path.write_text('[audio]\nmax_recording_seconds = 60\n[sounds]\nenabled = false\n[hotkey]\nkey = "f13"\n')
    controller.poll()
    assert general.sounds.isEnabled() and not on(appkit, general.sounds)
    assert general.limit.titleOfSelectedItem() == "1 min"
    assert controller.hotkey.key_field.title() == "F13"
    assert controller.word_field.isEnabled()
    assert general.footer.stringValue().startswith("Saved to ")


def test_mac_a_hand_edit_keeps_a_recording_going(appkit, mac_settings):
    controller = mac_settings()
    controller.hotkey.recordKey_(None)
    controller.path.write_text('[hotkey]\nkey = "f13"\n')
    controller.poll()
    assert controller.hotkey.recording == "key"
    assert controller.hotkey.key_field.title() == PRESS_KEY
    _tap(appkit, controller, 54)
    assert load_config(controller.path).hotkey == "cmd_r"


# -- Linux --------------------------------------------------------------------------


def _drain(gtk):
    context = gtk.GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


@pytest.fixture
def gtk_settings(gtk, tmp_path, memory_keyring):
    """Build Settings windows on tmp_path/config.toml, written first when given text.

    Work meant for a background thread runs inline, confirmations are answered yes, and
    Choose… picks whatever ``window.chosen`` holds.
    """
    from vox.ui.gtk.settings import SettingsWindow

    made = []
    devices = list(DEVICES)

    def build(text=None):
        path = tmp_path / "config.toml"
        if text is not None:
            path.write_text(text)
        settings = SettingsModel(path, devices=lambda channels: list(devices))
        window = SettingsWindow(settings, HotkeyModel(path), VocabModel(path), KeyModel())
        window.path = path
        window.devices = devices
        window.background = lambda work, done: done(work())
        window.confirmed = []

        def confirm(parent, title, message, button, destructive, then):
            window.confirmed.append(title)
            then()

        window.confirm = confirm
        window.chosen = None
        window.transcription.choose_file = lambda parent, current, then: then(window.chosen)
        made.append(window)
        return window

    yield build
    for window in made:
        if window.transcription.dialog is not None:
            window.transcription.dialog.force_close()
        window.destroy()
    _drain(gtk)


def combo_labels(combo):
    model = combo.row.get_model()
    return [model.get_string(i) for i in range(model.get_n_items())]


def combo_selected(combo):
    return combo.row.get_model().get_string(combo.row.get_selected())


def combo_pick(combo, text):
    combo.row.set_selected(combo_labels(combo).index(text))


def press(window, keyval):
    """A key press, with the key value standing in for the hardware keycode."""
    return window.hotkey.key_pressed(None, keyval, keyval, 0)


def release(window, keyval):
    window.hotkey.key_released(None, keyval, keyval, 0)


def tap(window, keyval):
    assert press(window, keyval) is True
    release(window, keyval)


# 6.1: the window and its pages


def test_gtk_has_the_five_pages_and_says_dictation_is_off(gtk, gtk_settings):
    window = gtk_settings()
    pages = window.stack.get_pages()
    names = [pages.get_item(i).get_name() for i in range(pages.get_n_items())]
    assert names == list(PAGES)
    assert window.stack.get_visible_child_name() == "general"
    window.show_page("snippets")
    assert window.stack.get_visible_child_name() == "snippets"
    assert not window.banner.get_revealed()


def test_gtk_run_opens_on_the_page_asked_for(gtk, tmp_path, memory_keyring):
    from vox.ui.gtk import settings

    path = tmp_path / "config.toml"
    with patch.object(settings, "run_app") as run_app:
        settings.run(SettingsModel(path, devices=lambda c: []), HotkeyModel(path), VocabModel(path), KeyModel(),
                     page="transcription")
    (app_id, make), _ = run_app.call_args
    assert app_id == settings.APP_ID
    window = make(None)
    assert window.stack.get_visible_child_name() == "transcription"
    window.destroy()
    _drain(gtk)


def test_gtk_opening_and_closing_without_a_change_creates_no_file(gtk, tmp_path, gtk_settings):
    window = gtk_settings()
    window.emit("close-request")
    _drain(gtk)
    assert not (tmp_path / "config.toml").exists()


# 6.2: General


def test_gtk_microphone_row_saves_the_device_by_name(gtk, gtk_settings):
    window = gtk_settings()
    microphone = window.general.microphone
    assert combo_labels(microphone) == [SYSTEM_DEFAULT, "MacBook Pro Microphone", "USB Audio Interface"]
    assert combo_selected(microphone) == SYSTEM_DEFAULT
    assert not window.path.exists()  # filling the rows saved nothing

    combo_pick(microphone, "USB Audio Interface")
    assert load_config(window.path).audio_device == "USB Audio Interface"
    combo_pick(microphone, SYSTEM_DEFAULT)
    assert load_config(window.path).audio_device is None


def test_gtk_refresh_lists_a_new_microphone_and_keeps_a_missing_one(gtk, gtk_settings):
    window = gtk_settings('[audio]\ndevice = "USB Audio Interface"\n')
    general = window.general
    window.devices[:] = [(0, "MacBook Pro Microphone"), (3, "AirPods")]
    general.refresh_button.emit("clicked")
    assert combo_labels(general.microphone) == [
        SYSTEM_DEFAULT, "MacBook Pro Microphone", "AirPods", f"USB Audio Interface {NOT_CONNECTED}",
    ]
    assert combo_selected(general.microphone) == f"USB Audio Interface {NOT_CONNECTED}"
    assert general.refresh_button.get_sensitive()
    assert not general.spinner.get_spinning()
    assert load_config(window.path).audio_device == "USB Audio Interface"


def test_gtk_recording_limit_row(gtk, gtk_settings):
    window = gtk_settings()
    limit = window.general.limit
    assert combo_selected(limit) == "15 min"
    combo_pick(limit, "30 min")
    assert load_config(window.path).max_recording_seconds == 1800


def test_gtk_switches_save_at_once(gtk, gtk_settings):
    window = gtk_settings()
    general = window.general
    sounds, lower, screen = general.sounds[0], general.lower[0], general.screen[0]
    assert sounds.get_active() and lower.get_active() and screen.get_active()

    sounds.set_active(False)
    screen.set_active(False)
    lower.set_active(False)
    config = load_config(window.path)
    assert (config.sounds_enabled, config.context_screen, config.attenuation_enabled) == (False, False, False)
    assert not general.level_row.get_sensitive()  # the level only matters while other audio is lowered

    lower.set_active(True)
    assert general.level_row.get_sensitive()


def test_gtk_level_saves_in_five_percent_steps_but_not_while_dragged(gtk, gtk_settings):
    window = gtk_settings()
    general = window.general
    general.dragging = True
    general.level.set_value(70)
    assert general.level_text.get_label() == "70%"
    assert not window.path.exists()

    general.dragging = False
    general.save_level()  # the release
    assert load_config(window.path).attenuation_level == pytest.approx(0.7)

    general.level.set_value(33)  # the keyboard or the scroll wheel, not a drag
    assert load_config(window.path).attenuation_level == pytest.approx(0.35)


def test_gtk_level_is_saved_once_the_pointer_lets_go(gtk, gtk_settings):
    window = gtk_settings()
    general = window.general

    def pointer(kind):
        assert general.pointer_event(None, SimpleNamespace(get_event_type=lambda: kind)) is False  # the scale acts too

    pointer(gtk.Gdk.EventType.BUTTON_PRESS)
    for value in (55, 60, 65):
        general.level.set_value(value)
    assert not window.path.exists()
    pointer(gtk.Gdk.EventType.BUTTON_RELEASE)
    _drain(gtk)
    assert load_config(window.path).attenuation_level == pytest.approx(0.65)


def test_gtk_a_level_from_the_file_that_is_not_a_step_is_kept(gtk, gtk_settings):
    window = gtk_settings("[attenuation]\nlevel = 0.33\n")
    assert window.general.level_text.get_label() == "33%"
    window.general.sounds[0].set_active(False)
    assert load_config(window.path).attenuation_level == pytest.approx(0.33)


# 6.3: Hotkey


def test_gtk_records_a_modifier_tapped_on_its_own_and_saves_it(gtk, gtk_settings):
    window = gtk_settings()
    page = window.hotkey
    assert page.key_field.get_label() == "Right Shift"
    page.key_field.emit("clicked")
    assert page.key_field.has_css_class("suggested-action")
    assert page.key_field.get_label() == PRESS_KEY
    assert page.status.get_label() == KEY_HINT and page.status.has_css_class("dim-label")

    tap(window, gtk.Gdk.KEY_Super_R)
    assert load_config(window.path).hotkey == "cmd_r"
    assert page.key_field.get_label() == "Right Super"
    assert not page.key_field.has_css_class("suggested-action")
    assert not page.status.get_visible()


def test_gtk_records_altgr_as_the_listener_names_it(gtk, gtk_settings):
    window = gtk_settings()
    window.hotkey.key_field.emit("clicked")
    tap(window, gtk.Gdk.KEY_ISO_Level3_Shift)
    assert load_config(window.path).hotkey == "vk_65027"
    assert window.hotkey.key_field.get_label() == "AltGr"


def test_gtk_records_pause_and_scroll_lock_and_keeps_them_out_of_combinations(gtk, gtk_settings):
    window = gtk_settings()
    page = window.hotkey
    page.key_field.emit("clicked")
    tap(window, gtk.Gdk.KEY_Pause)
    assert load_config(window.path).hotkey == "pause"
    assert not page.status.get_visible()  # no warning

    page.key_field.emit("clicked")
    tap(window, gtk.Gdk.KEY_Scroll_Lock)
    assert load_config(window.path).hotkey == "scroll_lock"

    page.combination_field.emit("clicked")
    press(window, gtk.Gdk.KEY_Control_L)
    press(window, gtk.Gdk.KEY_Pause)
    assert page.status.get_label() == BAD_COMBINATION
    assert page.recording == "combination"
    release(window, gtk.Gdk.KEY_Pause)
    release(window, gtk.Gdk.KEY_Control_L)
    assert load_config(window.path).hotkey_fallback == ""


def test_gtk_records_a_combination_and_clears_it(gtk, gtk_settings):
    window = gtk_settings()
    page = window.hotkey
    assert page.combination_field.get_label() == "None"
    assert not page.clear_button.get_visible()
    page.combination_field.emit("clicked")
    assert press(window, gtk.Gdk.KEY_Control_L) is True
    assert page.combination_field.get_label() == "Left Ctrl + …"
    assert press(window, gtk.Gdk.KEY_space) is True
    assert load_config(window.path).hotkey_fallback == "ctrl+space"
    assert page.combination_field.get_label() == "Left Ctrl + Space"
    assert page.clear_button.get_visible()

    page.clear_button.emit("clicked")
    assert load_config(window.path).hotkey_fallback == ""
    assert page.combination_field.get_label() == "None"


def test_gtk_records_alt_pressed_after_shift(gtk, gtk_settings):
    window = gtk_settings()
    window.hotkey.combination_field.emit("clicked")
    assert press(window, gtk.Gdk.KEY_Shift_L) is True
    assert press(window, gtk.Gdk.KEY_Meta_L) is True  # what X reports for Alt while Shift is held
    assert press(window, gtk.Gdk.KEY_space) is True
    assert load_config(window.path).hotkey_fallback == "alt+shift+space"


def test_gtk_refuses_a_typing_key_and_keeps_recording(gtk, gtk_settings):
    window = gtk_settings()
    page = window.hotkey
    page.key_field.emit("clicked")
    assert press(window, gtk.Gdk.KEY_a) is True  # swallowed, never typed
    assert page.status.get_label() == TYPING_KEY
    assert page.status.has_css_class("error")
    assert page.recording == "key"
    release(window, gtk.Gdk.KEY_a)
    assert not window.path.exists()

    tap(window, gtk.Gdk.KEY_Shift_L)
    assert load_config(window.path).hotkey == "shift"
    assert page.recording is None


def test_gtk_takes_every_key_while_recording(gtk, gtk_settings):
    """Ctrl+W, Escape and Ctrl+N would close the window or open the snippet editor if they got through."""
    window = gtk_settings()
    page = window.hotkey
    page.key_field.emit("clicked")
    for keyval in (gtk.Gdk.KEY_Control_L, gtk.Gdk.KEY_w, gtk.Gdk.KEY_n, gtk.Gdk.KEY_Return, gtk.Gdk.KEY_Tab):
        assert press(window, keyval) is True
    assert window.editor is None
    assert press(window, gtk.Gdk.KEY_Escape) is True  # stops recording, and only that
    assert page.recording is None
    assert page.key_field.get_label() == "Right Shift"
    assert not window.path.exists()


def test_gtk_keys_reach_the_window_while_not_recording(gtk, gtk_settings):
    window = gtk_settings()
    assert press(window, gtk.Gdk.KEY_a) is False
    assert press(window, gtk.Gdk.KEY_a) is False  # a held key repeats into a text field
    release(window, gtk.Gdk.KEY_a)
    assert press(window, gtk.Gdk.KEY_Escape) is False  # the window's own Escape shortcut

    window.hotkey.key_field.emit("clicked")
    assert press(window, gtk.Gdk.KEY_F13) is True
    assert load_config(window.path).hotkey == "f13" and window.hotkey.recording is None
    assert press(window, gtk.Gdk.KEY_F13) is True  # the key repeats while held: it must not press a button
    release(window, gtk.Gdk.KEY_F13)
    assert press(window, gtk.Gdk.KEY_Return) is False  # other keys reach the window again


def test_gtk_saves_each_recording_and_keeps_the_rest_of_the_file(gtk, gtk_settings):
    window = gtk_settings(CONFIG)
    page = window.hotkey
    page.key_field.emit("clicked")
    tap(window, gtk.Gdk.KEY_Control_R)
    page.combination_field.emit("clicked")
    press(window, gtk.Gdk.KEY_Super_L)
    press(window, gtk.Gdk.KEY_F5)
    config = load_config(window.path)
    assert (config.hotkey, config.hotkey_fallback) == ("right_ctrl", "cmd+f5")
    assert window.path.read_text().startswith("# my settings\n")


def test_gtk_a_clash_is_refused_and_nothing_is_saved(gtk, gtk_settings):
    window = gtk_settings()
    page = window.hotkey
    page.key_field.emit("clicked")
    tap(window, gtk.Gdk.KEY_Control_L)
    before = window.path.read_text()
    page.combination_field.emit("clicked")
    press(window, gtk.Gdk.KEY_Control_L)
    press(window, gtk.Gdk.KEY_space)
    assert page.status.get_label() == "The key combination can’t include the hotkey, Left Ctrl."
    assert page.status.has_css_class("error")
    assert page.combination_field.get_label() == "None"
    assert window.path.read_text() == before


def test_gtk_switching_pages_or_windows_stops_recording(gtk, gtk_settings):
    window = gtk_settings()
    window.show_page("hotkey")
    window.hotkey.key_field.emit("clicked")
    window.show_page("general")
    assert window.hotkey.recording is None

    window.show_page("hotkey")
    window.hotkey.combination_field.emit("clicked")
    window.hotkey.focus_lost()
    assert window.hotkey.recording is None
    assert not window.path.exists()


# 6.4: Transcription


def test_gtk_without_a_key_the_openai_modes_say_why(gtk, gtk_settings):
    window = gtk_settings()
    page = window.transcription
    assert page.mode_buttons["batch"].get_active() and page.mode_rows["batch"].get_sensitive()
    assert not page.mode_rows["streaming"].get_sensitive()
    assert page.mode_rows["streaming"].get_subtitle() == "Set an OpenAI API key before selecting OpenAI transcription"
    assert page.mode_warnings["batch"].get_visible()  # the saved mode can't run
    assert not page.mode_rows["whisper_cpp"].get_sensitive()
    assert page.key_button.get_label() == "Set Key…"
    assert not page.remove_key_button.get_visible()


def open_dialog(window, check=False):
    window.transcription.key_button.emit("clicked")
    dialog = window.transcription.dialog
    assert dialog is not None
    dialog.background = lambda work, done: done(work())
    dialog.confirmed = []

    def confirm(parent, title, message, button, destructive, then):
        dialog.confirmed.append(title)
        then()

    dialog.confirm = confirm
    dialog.check_row.set_active(check)
    return dialog


def test_gtk_a_key_saved_in_the_dialog_enables_the_openai_modes(gtk, memory_keyring, gtk_settings):
    window = gtk_settings()
    page = window.transcription
    dialog = open_dialog(window)
    dialog.entry.set_text(f" {KEY} ")
    dialog.save()
    _drain(gtk)
    assert stored(memory_keyring) == KEY
    assert page.dialog is None and dialog.closed
    assert page.mode_rows["streaming"].get_sensitive()
    assert page.key_row.get_subtitle() == "A key ending in 0001 is saved."
    assert page.key_button.get_label() == "Replace Key…"
    assert page.remove_key_button.get_visible()

    page.mode_buttons["streaming"].set_active(True)
    assert load_config(window.path).mode == "streaming"
    assert not page.mode_buttons["batch"].get_active()


def test_gtk_the_dialog_checks_with_openai_by_default(gtk, gtk_settings):
    window = gtk_settings()
    window.transcription.key_button.emit("clicked")
    assert window.transcription.dialog.check_row.get_active()


def test_gtk_cancel_closes_the_dialog_and_saves_nothing(gtk, memory_keyring, gtk_settings):
    window = gtk_settings()
    dialog = open_dialog(window)
    dialog.entry.set_text(KEY)
    dialog.finish()
    _drain(gtk)
    assert window.transcription.dialog is None
    assert stored(memory_keyring) is None


def test_gtk_escape_closes_the_dialog_and_saves_nothing(gtk, memory_keyring, gtk_settings):
    window = gtk_settings()
    dialog = open_dialog(window)
    dialog.entry.set_text(KEY)
    dialog.emit("closed")  # what Escape does to an Adw.Dialog
    assert window.transcription.dialog is None and dialog.closed
    assert stored(memory_keyring) is None
    window.transcription.key_button.emit("clicked")  # and it opens again
    assert window.transcription.dialog is not None


def test_gtk_blank_key_is_refused(gtk, memory_keyring, gtk_settings):
    window = gtk_settings()
    dialog = open_dialog(window)
    dialog.save()
    assert dialog.status.get_label() == "Paste your OpenAI API key."
    assert dialog.status.has_css_class("error")
    assert not dialog.closed


def test_gtk_key_with_an_invisible_character_is_refused(gtk, memory_keyring, gtk_settings):
    window = gtk_settings()
    dialog = open_dialog(window)
    dialog.entry.set_text("sk-test-dummy​0001")
    dialog.save()
    assert dialog.status.get_label() == BAD_CHARACTER
    assert stored(memory_keyring) is None


def test_gtk_rejected_key_is_not_saved(gtk, memory_keyring, gtk_settings):
    window = gtk_settings()
    dialog = open_dialog(window, check=True)
    dialog.entry.set_text(KEY)
    with patch("vox.ui.gtk.key.check_key", return_value=CheckResult(Outcome.REJECTED, "OpenAI didn’t accept this key.")):
        dialog.save()
    assert stored(memory_keyring) is None
    assert dialog.status.get_label() == "OpenAI didn’t accept this key."
    assert dialog.save_button.get_sensitive()
    assert not dialog.closed


def test_gtk_unchecked_key_can_be_saved_anyway(gtk, memory_keyring, gtk_settings):
    window = gtk_settings()
    dialog = open_dialog(window, check=True)
    dialog.entry.set_text(KEY)
    with patch("vox.ui.gtk.key.check_key", return_value=CheckResult(Outcome.UNCHECKED, "Vox Transfer couldn’t reach OpenAI.")):
        dialog.save()
    assert dialog.confirmed == ["Couldn’t Check the Key"]
    assert stored(memory_keyring) == KEY


def test_gtk_remove_is_offered_only_for_a_saved_key(gtk, memory_keyring, gtk_settings):
    keystore.set_api_key(KEY)
    window = gtk_settings('[transcription]\nmode = "streaming"\n')
    page = window.transcription
    assert page.remove_key_button.get_visible()
    page.remove_key_button.emit("clicked")
    assert window.confirmed == ["Remove the Saved Key?"]
    assert stored(memory_keyring) is None
    assert not page.remove_key_button.get_visible()
    assert not page.mode_rows["batch"].get_sensitive()
    assert page.mode_rows["streaming"].get_sensitive()  # still the saved mode, now with its warning
    assert page.mode_warnings["streaming"].get_visible()


def test_gtk_keychain_error_keeps_the_dialog_open(gtk, memory_keyring, gtk_settings):
    window = gtk_settings()
    dialog = open_dialog(window)
    dialog.entry.set_text(KEY)
    with patch.object(KeyModel, "save", side_effect=keystore.KeystoreError("No keyring daemon.")), \
         patch("vox.ui.gtk.key.error_dialog") as alert:
        dialog.save()
    alert.assert_called_once()
    assert not dialog.closed and window.transcription.dialog is dialog


def test_gtk_warns_when_the_key_would_not_be_encrypted(gtk, gtk_settings):
    keyring.set_keyring(fail.Keyring())
    window = gtk_settings()
    window.transcription.key_button.emit("clicked")
    assert window.transcription.dialog.banner.get_revealed()


def test_gtk_language_row(gtk, gtk_settings):
    window = gtk_settings()
    language = window.transcription.language
    assert combo_selected(language) == DETECT_LANGUAGE
    combo_pick(language, "German")
    assert load_config(window.path).whisper_language == "de"
    combo_pick(language, DETECT_LANGUAGE)
    assert load_config(window.path).whisper_language is None


def test_gtk_prompt_is_saved_on_apply_and_when_the_window_closes(gtk, gtk_settings):
    window = gtk_settings()
    prompt = window.transcription.prompt
    prompt.set_text("Vox Transfer, FastAPI.")
    assert not window.path.exists()  # not on every keystroke
    prompt.emit("apply")
    assert load_config(window.path).whisper_prompt == "Vox Transfer, FastAPI."

    prompt.set_text("Kubernetes.")
    window.emit("close-request")  # what closing does; close() sends it only to a window on screen
    assert load_config(window.path).whisper_prompt == "Kubernetes."


def test_gtk_choosing_the_whisper_cpp_files_enables_local_mode(gtk, tmp_path, gtk_settings):
    binary, ggml = local_setup(tmp_path)
    window = gtk_settings()
    page = window.transcription
    assert page.model_row.get_subtitle() == "None chosen"

    window.chosen = str(binary)
    page.binary_row.button.emit("clicked")
    window.chosen = str(ggml)
    page.model_row.button.emit("clicked")
    config = load_config(window.path)
    assert (config.whisper_cpp_binary, config.whisper_cpp_model) == (str(binary), str(ggml))
    assert page.mode_rows["whisper_cpp"].get_sensitive()

    page.mode_buttons["whisper_cpp"].set_active(True)
    assert load_config(window.path).mode == "whisper_cpp"


def test_gtk_a_save_that_fails_is_explained_and_the_page_shows_the_file(gtk, gtk_settings):
    window = gtk_settings()
    with patch("vox.ui.settings_model.update_flag", side_effect=OSError("disk full")), \
         patch("vox.ui.gtk.settings.error_dialog") as alert:
        window.general.sounds[0].set_active(False)
    assert alert.call_args.args[1:] == ("Couldn’t Save", "disk full")
    assert window.general.sounds[0].get_active()


# Vocabulary and Snippets, as in the vocabulary window


def gtk_titles(listbox):
    titles, i = [], 0
    while (row := listbox.get_row_at_index(i)) is not None:
        titles.append(row.get_title())
        i += 1
    return titles


def test_gtk_lists_words_and_snippets_and_adds_words(gtk, gtk_settings):
    window = gtk_settings(CONFIG)
    assert gtk_titles(window.words_list) == ["FastAPI"]
    assert gtk_titles(window.snippets_list) == ["my email"]
    window.word_entry.set_text("Kubernetes, PostgreSQL")
    window.add_words()
    assert load_config(window.path).dictionary == ["FastAPI", "Kubernetes", "PostgreSQL"]


def test_gtk_snippet_editor(gtk, gtk_settings):
    window = gtk_settings(CONFIG)
    window.open_editor(None)
    assert window.stack.get_visible_child_name() == "snippets"
    editor = window.editor
    editor.trigger.set_text("sign off")
    editor.expansion.get_buffer().set_text("Best,\nAlex")
    editor.save()
    _drain(gtk)
    assert load_config(window.path).snippets["sign off"] == "Best,\nAlex"


# 6.5: a file that can't be read, and edits made by hand


def test_gtk_a_broken_file_shows_a_banner_and_disables_the_settings_but_not_the_key(gtk, memory_keyring, gtk_settings):
    with patch("vox.ui.gtk.vocab.error_dialog") as dialog:
        window = gtk_settings("[audio\n")
    dialog.assert_called_once()
    assert window.banner.get_revealed()
    assert window.banner.get_title().startswith("Couldn’t read ")
    general, page, hotkey = window.general, window.transcription, window.hotkey
    for widget in (general.microphone_row, general.limit_row, general.sounds[0], general.screen[0], general.level_row,
                   page.language_row, page.prompt, page.binary_row, *page.mode_rows.values(),
                   hotkey.key_field, hotkey.combination_field, window.word_entry, window.new_button):
        assert not widget.get_sensitive()

    assert page.key_button.get_sensitive()
    dialog = open_dialog(window)
    dialog.entry.set_text(KEY)
    dialog.save()
    assert stored(memory_keyring) == KEY
    assert window.path.read_text() == "[audio\n"


def test_gtk_breaking_and_fixing_the_file_while_open(gtk, gtk_settings):
    window = gtk_settings("[audio]\nmax_recording_seconds = 120\n")
    general = window.general
    assert not window.settings.poll()

    window.path.write_text("[audio\nmax_recording_seconds = 60\n")
    with patch("vox.ui.gtk.settings.error_dialog") as alert, patch("vox.ui.gtk.vocab.error_dialog") as vocab_alert:
        window.poll()
    alert.assert_not_called()
    vocab_alert.assert_not_called()
    assert window.banner.get_revealed()
    assert not general.sounds[0].get_sensitive()
    assert not window.hotkey.key_field.get_sensitive()
    assert not window.word_entry.get_sensitive()
    assert combo_selected(general.limit) == "2 min"  # the last settings read stay shown

    window.path.write_text('[audio]\nmax_recording_seconds = 60\n[sounds]\nenabled = false\n[hotkey]\nkey = "f13"\n')
    window.poll()
    assert not window.banner.get_revealed()
    assert general.sounds[0].get_sensitive() and not general.sounds[0].get_active()
    assert combo_selected(general.limit) == "1 min"
    assert window.hotkey.key_field.get_label() == "F13"
    assert window.word_entry.get_sensitive()
    assert window.path.read_text().endswith('key = "f13"\n')  # showing the edit wrote nothing
