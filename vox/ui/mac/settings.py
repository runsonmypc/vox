"""Settings window for macOS: General, Hotkey, Transcription, Vocabulary and Snippets toolbar tabs.

Built on the vocabulary window's tab controller. Every change is saved to config.toml
as it is made, with no Save button; the API key keeps its own sheet with Save. The
file is read again every 2 seconds, so a hand edit shows while the window is open.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import AppKit
import objc
from Foundation import NSObject
from PyObjCTools import AppHelper

from ..hotkey_model import HotkeyModel
from ..key_model import REMOVE_BUTTON, REMOVE_FAILED_TITLE, REMOVE_MESSAGE, REMOVE_TITLE, KeyModel
from ..settings_model import (
    CHOOSE,
    DICTATION_OFF,
    KEEP_FAILED_AUDIO,
    KEEP_FAILED_AUDIO_NOTE,
    LANGUAGE,
    LOWER_AUDIO,
    LOWER_AUDIO_LEVEL,
    MICROPHONE,
    MODE,
    PRIVACY_LINK,
    PRIVACY_URL,
    PROMPT,
    PROMPT_NOTE,
    RECORDING_LIMIT,
    RECORDING_LIMIT_NOTE,
    SAVE_FAILED_TITLE,
    SCREEN_HINTS,
    SCREEN_HINTS_NOTE,
    SOUNDS,
    SOUNDS_NOTE,
    TITLE,
    WHISPER_BINARY,
    WHISPER_MODEL,
    SettingsModel,
    percent,
)
from ..vocab_model import VocabModel
from . import kit
from .hotkey import HotkeyPage
from .key import KeyController
from .vocab import VocabController

log = logging.getLogger(__name__)

_SIZE = (580, 540)
_PAD = 20
_POLL_SECONDS = 2.0
_NOTE_WIDTH = 330


def _in_background(work: Callable[[], object], done: Callable[[object], None]) -> None:
    threading.Thread(target=lambda: AppHelper.callAfter(done, work()), daemon=True).start()


def _current_event() -> AppKit.NSEvent | None:
    return AppKit.NSApplication.sharedApplication().currentEvent()


def _choose_file(window: AppKit.NSWindow, current: str, then: Callable[[str], None]) -> None:
    """Ask for a file in a sheet, starting next to ``current``, and call ``then`` with the path chosen."""
    panel = AppKit.NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(False)
    folder = Path(current).expanduser().parent if current else None
    if folder is not None and folder.is_dir():
        panel.setDirectoryURL_(AppKit.NSURL.fileURLWithPath_(str(folder)))

    def done(response: int) -> None:
        if response == AppKit.NSModalResponseOK and panel.URL() is not None:
            then(panel.URL().path())

    panel.beginSheetModalForWindow_completionHandler_(window, done)


def _note(text: str) -> AppKit.NSTextField:
    field = kit.label(text, 11, color=AppKit.NSColor.secondaryLabelColor(), wrap=True)
    field.setPreferredMaxLayoutWidth_(_NOTE_WIDTH)
    return field


def _checkbox(title: str, target: NSObject, action: str) -> AppKit.NSButton:
    """A checkbox whose title wraps at the notes' width, so a long one can't squeeze the labels."""
    button = AppKit.NSButton.checkboxWithTitle_target_action_(title, target, action)
    button.cell().setWraps_(True)
    button.cell().setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    button.setTranslatesAutoresizingMaskIntoConstraints_(False)
    kit.constrain(button.widthAnchor().constraintLessThanOrEqualToConstant_(_NOTE_WIDTH + 20))
    return button


def _form_label(title: str | None) -> AppKit.NSView:
    if title is None:
        return AppKit.NSGridCell.emptyContentView()
    field = kit.label(f"{title}:" if title else "", 13)
    field.setContentCompressionResistancePriority_forOrientation_(
        AppKit.NSLayoutPriorityRequired, AppKit.NSLayoutConstraintOrientationHorizontal
    )
    return field


def _on(button: AppKit.NSButton) -> bool:
    return button.state() == AppKit.NSControlStateValueOn


def _set_on(button: AppKit.NSButton, on: bool) -> None:
    button.setState_(AppKit.NSControlStateValueOn if on else AppKit.NSControlStateValueOff)


def _fill(popup: AppKit.NSPopUpButton, labels: list[str], selected: int) -> None:
    popup.removeAllItems()
    for text in labels:
        popup.addItemWithTitle_("")  # addItemsWithTitles_ drops duplicates, and two devices can share a name
        popup.lastItem().setTitle_(text)
    popup.selectItemAtIndex_(selected)


def _form(rows: list[tuple[str, AppKit.NSView | None]]) -> AppKit.NSGridView:
    """System Settings' two columns: right-aligned labels, and the controls lined up after them."""
    grid = AppKit.NSGridView.gridViewWithViews_([
        [_form_label(title), view or AppKit.NSGridCell.emptyContentView()] for title, view in rows
    ])
    grid.setRowSpacing_(8)
    grid.setColumnSpacing_(10)
    grid.columnAtIndex_(0).setXPlacement_(AppKit.NSGridCellPlacementTrailing)
    grid.setRowAlignment_(AppKit.NSGridRowAlignmentFirstBaseline)
    grid.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return grid


def _page(grid: AppKit.NSGridView, footer: AppKit.NSTextField) -> AppKit.NSView:
    view = kit.container(*_SIZE)
    for sub in (grid, footer):
        view.addSubview_(sub)
    kit.constrain(
        grid.topAnchor().constraintEqualToAnchor_constant_(view.topAnchor(), _PAD + 4),
        grid.centerXAnchor().constraintEqualToAnchor_(view.centerXAnchor()),
        grid.leadingAnchor().constraintGreaterThanOrEqualToAnchor_constant_(view.leadingAnchor(), _PAD),
        footer.topAnchor().constraintGreaterThanOrEqualToAnchor_constant_(grid.bottomAnchor(), 16),
        footer.leadingAnchor().constraintEqualToAnchor_constant_(view.leadingAnchor(), _PAD),
        footer.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(view.trailingAnchor(), -_PAD),
        view.bottomAnchor().constraintEqualToAnchor_constant_(footer.bottomAnchor(), 16),
    )
    return view


class SettingsTabs(AppKit.NSTabViewController):
    """A tab controller that tells the Settings window when the user switches tab."""

    def tabView_didSelectTabViewItem_(self, tab_view, item) -> None:
        objc.super(SettingsTabs, self).tabView_didSelectTabViewItem_(tab_view, item)
        owner = getattr(self, "owner", None)
        if owner is not None:
            owner.tab_selected(item.identifier())


class GeneralPage(NSObject):
    def initWithOwner_(self, owner: SettingsController) -> GeneralPage:
        self = objc.super(GeneralPage, self).init()
        if self is None:
            return None
        self.owner = owner
        self.model: SettingsModel = owner.settings
        self._build()
        return self

    @objc.python_method
    def _build(self) -> None:
        self.microphone = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(AppKit.NSZeroRect, False)
        self.microphone.setTarget_(self)
        self.microphone.setAction_("microphoneChanged:")
        kit.constrain(self.microphone.widthAnchor().constraintEqualToConstant_(260))
        self.refresh_button = kit.icon_button("arrow.clockwise", self, "refreshDevices:", "Look for microphones again")
        self.spinner = AppKit.NSProgressIndicator.alloc().init()
        self.spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        self.spinner.setControlSize_(AppKit.NSControlSizeSmall)
        self.spinner.setDisplayedWhenStopped_(False)
        self.spinner.setTranslatesAutoresizingMaskIntoConstraints_(False)
        microphone_row = kit.stack([self.microphone, self.refresh_button, self.spinner], vertical=False, spacing=6)

        self.limit = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(AppKit.NSZeroRect, False)
        self.limit.setTarget_(self)
        self.limit.setAction_("limitChanged:")

        self.keep_failed_audio = _checkbox(KEEP_FAILED_AUDIO, self, "recoveryChanged:")
        self.sounds = _checkbox(SOUNDS_NOTE, self, "soundsChanged:")
        self.lower = _checkbox(LOWER_AUDIO, self, "lowerChanged:")
        self.level = AppKit.NSSlider.sliderWithValue_minValue_maxValue_target_action_(50, 0, 100, self, "levelChanged:")
        self.level.setNumberOfTickMarks_(21)  # 5% steps
        kit.constrain(self.level.widthAnchor().constraintEqualToConstant_(200))
        self.level_text = kit.label("", 13, color=AppKit.NSColor.secondaryLabelColor())
        level_row = kit.stack([self.level, self.level_text], vertical=False, spacing=8)

        self.screen = _checkbox(SCREEN_HINTS_NOTE, self, "screenChanged:")
        link = AppKit.NSButton.buttonWithTitle_target_action_(PRIVACY_LINK, self, "openPrivacy:")
        link.setBezelStyle_(AppKit.NSBezelStyleInline)
        link.setBordered_(False)
        link.setContentTintColor_(AppKit.NSColor.linkColor())

        self.footer = kit.label("", 11, color=AppKit.NSColor.tertiaryLabelColor(), wrap=True)
        self.view = _page(_form([
            (MICROPHONE, microphone_row),
            (RECORDING_LIMIT, self.limit),
            (None, _note(RECORDING_LIMIT_NOTE)),
            ("Recovery", self.keep_failed_audio),
            (None, _note(KEEP_FAILED_AUDIO_NOTE)),
            (SOUNDS, self.sounds),
            ("Other audio", self.lower),
            (LOWER_AUDIO_LEVEL, level_row),
            (SCREEN_HINTS, self.screen),
            (None, link),
        ]), self.footer)

    @objc.python_method
    def render(self) -> None:
        model, config = self.model, self.model.config
        writable = model.writable
        _fill(self.microphone, *self._labels(model.microphones()))
        _fill(self.limit, *self._labels(model.limits()))
        _set_on(self.keep_failed_audio, config.keep_failed_audio)
        _set_on(self.sounds, config.sounds_enabled)
        _set_on(self.lower, config.attenuation_enabled)
        self.level.setDoubleValue_(percent(config.attenuation_level))
        self.level_text.setStringValue_(f"{percent(config.attenuation_level)}%")
        _set_on(self.screen, config.context_screen)
        for control in (self.microphone, self.refresh_button, self.limit, self.sounds, self.lower, self.screen, self.keep_failed_audio):
            control.setEnabled_(writable)
        self.level.setEnabled_(writable and config.attenuation_enabled)
        self.owner.set_footer(self.footer)

    @staticmethod
    def _labels(choices_selected) -> tuple[list[str], int]:
        choices, selected = choices_selected
        return [choice.label for choice in choices], selected

    def microphoneChanged_(self, sender) -> None:
        self.owner.changed(self.model.choose_microphone(self.microphone.indexOfSelectedItem()))

    def refreshDevices_(self, sender) -> None:
        self.refresh_button.setEnabled_(False)
        self.spinner.startAnimation_(None)

        def done(_result: object) -> None:
            self.spinner.stopAnimation_(None)
            self.render()

        self.owner.background(self.model.refresh_devices, done)

    def limitChanged_(self, sender) -> None:
        choices, _ = self.model.limits()
        self.owner.changed(self.model.set_limit(choices[self.limit.indexOfSelectedItem()].value))

    def recoveryChanged_(self, sender) -> None:
        self.owner.changed(self.model.set_keep_failed_audio(_on(self.keep_failed_audio)))

    def soundsChanged_(self, sender) -> None:
        self.owner.changed(self.model.set_sounds(_on(self.sounds)))

    def lowerChanged_(self, sender) -> None:
        self.owner.changed(self.model.set_attenuation(_on(self.lower)))

    def levelChanged_(self, sender) -> None:
        value = self.level.doubleValue()
        self.level_text.setStringValue_(f"{round(value)}%")
        event = _current_event()
        if event is not None and event.type() in (AppKit.NSEventTypeLeftMouseDragged, AppKit.NSEventTypeLeftMouseDown):
            return  # saved once, when the slider is released
        self.owner.changed(self.model.set_attenuation_percent(value))

    def screenChanged_(self, sender) -> None:
        self.owner.changed(self.model.set_screen_hints(_on(self.screen)))

    def openPrivacy_(self, sender) -> None:
        AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(PRIVACY_URL))


class TranscriptionPage(NSObject):
    def initWithOwner_(self, owner: SettingsController) -> TranscriptionPage:
        self = objc.super(TranscriptionPage, self).init()
        if self is None:
            return None
        self.owner = owner
        self.model: SettingsModel = owner.settings
        self.key_model: KeyModel = owner.key_model
        self.sheet: KeyController | None = None
        self.choose_file = _choose_file  # replaced in tests
        self._build()
        return self

    @objc.python_method
    def _build(self) -> None:
        secondary = AppKit.NSColor.secondaryLabelColor()
        self.mode_buttons: dict[str, AppKit.NSButton] = {}
        self.mode_reasons: dict[str, AppKit.NSTextField] = {}
        mode_views = []
        for choice in self.model.modes():
            button = AppKit.NSButton.radioButtonWithTitle_target_action_(choice.label, self, "modeChanged:")
            reason = _note("")
            self.mode_buttons[choice.mode] = button
            self.mode_reasons[choice.mode] = reason
            mode_views += [button, reason]
        modes = kit.stack(mode_views, spacing=4)

        self.language = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(AppKit.NSZeroRect, False)
        self.language.setTarget_(self)
        self.language.setAction_("languageChanged:")

        self.prompt = kit.text_field("")
        self.prompt.setControlSize_(AppKit.NSControlSizeRegular)
        self.prompt.setTarget_(self)
        self.prompt.setAction_("promptChanged:")  # on Return, and when the field is left
        kit.constrain(self.prompt.widthAnchor().constraintEqualToConstant_(300))

        self.key_text = kit.label("", 13, color=secondary)
        self.key_button = AppKit.NSButton.buttonWithTitle_target_action_("Set Key…", self, "setKey:")
        self.remove_key_button = AppKit.NSButton.buttonWithTitle_target_action_("Remove…", self, "removeKey:")
        key_row = kit.stack([self.key_text, self.key_button, self.remove_key_button], vertical=False, spacing=8)

        self.binary_text = kit.label("", 12, color=secondary)
        self.model_text = kit.label("", 12, color=secondary)
        self.binary_button = AppKit.NSButton.buttonWithTitle_target_action_(CHOOSE, self, "chooseBinary:")
        self.model_button = AppKit.NSButton.buttonWithTitle_target_action_(CHOOSE, self, "chooseModel:")
        for text in (self.binary_text, self.model_text):
            text.setLineBreakMode_(AppKit.NSLineBreakByTruncatingMiddle)
            kit.constrain(text.widthAnchor().constraintLessThanOrEqualToConstant_(230))

        self.footer = kit.label("", 11, color=AppKit.NSColor.tertiaryLabelColor(), wrap=True)
        self.view = _page(_form([
            (MODE, modes),
            ("OpenAI API key", key_row),
            (LANGUAGE, self.language),
            (PROMPT, self.prompt),
            (None, _note(PROMPT_NOTE)),
            (WHISPER_BINARY, kit.stack([self.binary_button, self.binary_text], vertical=False, spacing=8)),
            (WHISPER_MODEL, kit.stack([self.model_button, self.model_text], vertical=False, spacing=8)),
        ]), self.footer)

    @objc.python_method
    def render(self, *, keep_prompt: bool = False) -> None:
        model, config = self.model, self.model.config
        writable = model.writable
        for choice in model.modes():
            button, reason = self.mode_buttons[choice.mode], self.mode_reasons[choice.mode]
            _set_on(button, choice.mode == config.mode)
            button.setEnabled_(writable and choice.enabled)
            reason.setStringValue_(choice.problem or "")
            reason.setHidden_(choice.problem is None)
            saved_cannot_run = choice.mode == config.mode and choice.problem is not None
            reason.setTextColor_(AppKit.NSColor.systemRedColor() if saved_cannot_run else AppKit.NSColor.secondaryLabelColor())
        choices, selected = model.languages()
        _fill(self.language, [choice.label for choice in choices], selected)
        if not (keep_prompt and self.editing_prompt):
            self.prompt.setStringValue_(config.whisper_prompt)
        self.binary_text.setStringValue_(config.whisper_cpp_binary)
        self.model_text.setStringValue_(config.whisper_cpp_model or "None chosen")
        for control in (self.language, self.prompt, self.binary_button, self.model_button):
            control.setEnabled_(writable)

        key = self.key_model
        key.reload()
        self.key_text.setStringValue_(key.saved_text)
        self.key_text.setTextColor_(AppKit.NSColor.systemRedColor() if key.read_error else AppKit.NSColor.secondaryLabelColor())
        self.key_button.setTitle_("Replace Key…" if key.has_saved_key else "Set Key…")
        self.remove_key_button.setHidden_(not key.has_saved_key)
        self.owner.set_footer(self.footer)

    @property
    def editing_prompt(self) -> bool:
        return self.prompt.currentEditor() is not None

    @objc.python_method
    def commit_prompt(self) -> None:
        """Save the prompt if it was edited: on Return, when the field is left, and when the window closes."""
        text = self.prompt.stringValue()
        if text != self.model.config.whisper_prompt and self.model.writable:
            self.owner.changed(self.model.set_prompt(text))

    def modeChanged_(self, sender) -> None:
        mode = next(mode for mode, button in self.mode_buttons.items() if button == sender)
        self.owner.changed(self.model.set_mode(mode))

    def languageChanged_(self, sender) -> None:
        self.owner.changed(self.model.choose_language(self.language.indexOfSelectedItem()))

    def promptChanged_(self, sender) -> None:
        self.commit_prompt()

    def chooseBinary_(self, sender) -> None:
        self.choose_file(self.owner.window, self.model.config.whisper_cpp_binary,
                         lambda path: self.owner.changed(self.model.set_whisper_binary(path)))

    def chooseModel_(self, sender) -> None:
        self.choose_file(self.owner.window, self.model.config.whisper_cpp_model,
                         lambda path: self.owner.changed(self.model.set_whisper_model(path)))

    def setKey_(self, sender) -> None:
        if self.sheet is not None:
            return
        sheet = self.sheet = KeyController.alloc().initWithModel_(self.key_model)
        sheet.on_finish = self.sheet_done
        self.owner.window.beginSheet_completionHandler_(sheet.window, None)
        sheet.window.makeFirstResponder_(sheet.secure_field)

    @objc.python_method
    def sheet_done(self) -> None:
        if self.sheet is not None:
            self.owner.window.endSheet_(self.sheet.window)
            self.sheet = None
        self.key_changed()

    def removeKey_(self, sender) -> None:
        self.owner.confirm(self.owner.window, REMOVE_TITLE, REMOVE_MESSAGE, REMOVE_BUTTON, True, self.remove_key)

    @objc.python_method
    def remove_key(self) -> None:
        try:
            self.key_model.remove()
        except Exception as e:
            self.owner.alert(REMOVE_FAILED_TITLE, str(e))
            return
        log.info("Removed the OpenAI API key")
        self.key_changed()

    @objc.python_method
    def key_changed(self) -> None:
        """A key saved or removed decides at once whether the OpenAI modes can be chosen."""
        self.model.reload_key()
        self.render(keep_prompt=True)


class SettingsController(VocabController):
    def initWithSettings_hotkey_vocab_key_(
        self, settings: SettingsModel, hotkey: HotkeyModel, vocab: VocabModel, key: KeyModel
    ) -> SettingsController:
        # Set before the vocabulary window's init, which builds every tab
        self.settings = settings
        self.hotkey_model = hotkey
        self.key_model = key
        self.background = _in_background  # replaced in tests
        self.confirm = kit.confirm
        settings.reload()
        settings.reload_key()
        settings.refresh_devices()  # before the General tab shows: PortAudio's first look can take a moment
        hotkey.reload()
        self = objc.super(SettingsController, self).initWithModel_(vocab)
        if self is None:
            return None
        self.window.setTitle_(TITLE)
        self.window.setSubtitle_(DICTATION_OFF)
        self.render_pages()
        self.timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            _POLL_SECONDS, True, lambda _timer: self.poll()
        )
        return self

    # -- Layout -----------------------------------------------------------

    @objc.python_method
    def tab_controller(self) -> AppKit.NSTabViewController:
        tabs = SettingsTabs.alloc().init()
        tabs.owner = self
        return tabs

    @objc.python_method
    def vocab_tabs(self) -> list:
        self.general = GeneralPage.alloc().initWithOwner_(self)
        self.hotkey = HotkeyPage.alloc().initWithModel_alert_(self.hotkey_model, self.alert)
        self.transcription = TranscriptionPage.alloc().initWithOwner_(self)
        return [
            ("general", "General", "gearshape", self.general.view),
            ("hotkey", "Hotkey", "keyboard", self.hotkey.view),
            ("transcription", "Transcription", "waveform", self.transcription.view),
            *VocabController.vocab_tabs(self),
        ]

    # -- Rendering --------------------------------------------------------

    @objc.python_method
    def render_pages(self, *, keep_prompt: bool = False) -> None:
        self.general.render()
        self.hotkey.render()
        self.transcription.render(keep_prompt=keep_prompt)

    @objc.python_method
    def set_footer(self, footer: AppKit.NSTextField) -> None:
        model = self.settings
        if model.writable:
            footer.setStringValue_(f"Saved to {model.shown_path} as you make each change.")
            footer.setTextColor_(AppKit.NSColor.tertiaryLabelColor())
        else:
            footer.setStringValue_(model.unreadable)
            footer.setTextColor_(AppKit.NSColor.systemRedColor())

    @objc.python_method
    def changed(self, error: str | None) -> None:
        """After a change: say why it couldn't be saved, if so, and show what the file holds now."""
        if error is not None:
            self.alert(SAVE_FAILED_TITLE, error)
        self.render_pages(keep_prompt=True)

    @objc.python_method
    def alert(self, title: str, message: str) -> None:
        kit.alert(self.window, title, message)

    @objc.python_method
    def poll(self) -> None:
        """Show a hand edit of config.toml, keeping a prompt being typed and a hotkey being recorded."""
        if not self.settings.poll():
            return
        self.model.reload()
        self.render()  # the Vocabulary and Snippets tabs
        if self.hotkey.recording is None:
            self.hotkey_model.reload()
        self.render_pages(keep_prompt=True)

    # -- Tabs, keys and closing -------------------------------------------

    @objc.python_method
    def tab_selected(self, identifier: str) -> None:
        if identifier != "hotkey" and self.hotkey.recording is not None:
            self.hotkey.record(None)

    @objc.python_method
    def monitored_events(self) -> int:
        return AppKit.NSEventMaskKeyDown | AppKit.NSEventMaskKeyUp | AppKit.NSEventMaskFlagsChanged

    @objc.python_method
    def handle_key(self, event: AppKit.NSEvent) -> AppKit.NSEvent | None:
        if event.window() == self.window and self.hotkey.handle_key(event) is None:
            return None  # the Hotkey tab took it
        if event.type() != AppKit.NSEventTypeKeyDown:
            return event
        return VocabController.handle_key(self, event)

    def windowDidResignKey_(self, notification) -> None:
        if self.hotkey.recording is not None:
            self.hotkey.record(None)

    def windowWillClose_(self, notification) -> None:
        self.transcription.commit_prompt()
        self.timer.invalidate()
        objc.super(SettingsController, self).windowWillClose_(notification)


def run(settings: SettingsModel, hotkey: HotkeyModel, vocab: VocabModel, key: KeyModel, page: str = "general") -> None:
    kit.application()
    controller = SettingsController.alloc().initWithSettings_hotkey_vocab_key_(settings, hotkey, vocab, key)
    controller.select_tab(page)
    kit.run(controller.window)
