"""Hotkey window for macOS: two fields that record keys, Use Default, Cancel and Save.

A field records after it is clicked. While it does, a local event monitor takes
every key, so Return doesn't save and Esc stops recording instead of closing;
left and right modifiers are told apart by the device bits of flags-changed events.
"""

from __future__ import annotations

import logging

import AppKit
import objc
from Foundation import NSObject

from ...errors import ConfigError
from ..hotkey_model import (
    CLEAR_TOOLTIP,
    COMBINATION_NOTE,
    COMBINATION_ROW,
    INTRO,
    KEY_ROW,
    LOAD_FAILED_TITLE,
    MAC_KEYS,
    PRESS_KEY,
    PRESS_KEYS,
    SAVE_FAILED_TITLE,
    TITLE,
    USE_DEFAULT,
    Capture,
    HotkeyModel,
    combination_label,
    label,
)
from . import kit

log = logging.getLogger(__name__)

_WIDTH = 440
_PAD = 20
_TEXT_WIDTH = _WIDTH - 2 * _PAD
_LABEL_WIDTH = 120
_FIELD_WIDTH = 250
_SPACING = 8

# Key codes of the modifiers -> their NX_DEVICE*KEYMASK bit in modifierFlags, set while that key is down;
# fn, a single key, sets the function flag
_DOWN = {
    59: 0x01, 56: 0x02, 60: 0x04, 55: 0x08, 54: 0x10, 58: 0x20, 61: 0x40, 62: 0x2000,
    63: AppKit.NSEventModifierFlagFunction,
}
_STATUS_COLORS = {"error": AppKit.NSColor.systemRedColor(), "warning": AppKit.NSColor.systemOrangeColor()}


class HotkeyController(NSObject):
    def initWithModel_(self, model: HotkeyModel) -> HotkeyController:
        self = objc.super(HotkeyController, self).init()
        if self is None:
            return None
        self.model = model
        self.closed = False
        self.recording: str | None = None  # the field recording keys: "key" or "combination"
        self.refusal: str | None = None
        self.capture = Capture()
        self._build()
        model.reload()
        self.render()
        if model.load_error:
            kit.alert(self.window, LOAD_FAILED_TITLE, model.load_error)
        self._monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskKeyDown | AppKit.NSEventMaskFlagsChanged, self.handle_key
        )
        return self

    # -- Layout -----------------------------------------------------------

    @objc.python_method
    def _build(self) -> None:
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, _WIDTH, 240),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered, False,
        )
        self.window.setTitle_(TITLE)
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        view = self.window.contentView()
        secondary = AppKit.NSColor.secondaryLabelColor()

        intro = self._wrapping(INTRO, 13, secondary, _TEXT_WIDTH)
        self.key_field = self._field("recordKey:")
        self.combination_field = self._field("recordCombination:")
        self.clear_button = kit.icon_button("xmark.circle.fill", self, "clearCombination:", CLEAR_TOOLTIP)
        note = self._wrapping(COMBINATION_NOTE, 12, secondary, _TEXT_WIDTH - _LABEL_WIDTH - _SPACING)
        rows = [
            self._row(f"{KEY_ROW}:", self.key_field),
            self._row(f"{COMBINATION_ROW}:", kit.stack([self.combination_field, self.clear_button], vertical=False)),
            self._row("", note),
        ]
        self.status = self._wrapping("", 12, secondary, _TEXT_WIDTH)

        self.default_button = AppKit.NSButton.buttonWithTitle_target_action_(USE_DEFAULT, self, "useDefault:")
        self.save_button = AppKit.NSButton.buttonWithTitle_target_action_("Save", self, "save:")
        self.save_button.setKeyEquivalent_("\r")
        cancel = AppKit.NSButton.buttonWithTitle_target_action_("Cancel", self, "cancel:")
        cancel.setKeyEquivalent_("\x1b")
        spacer = AppKit.NSView.alloc().init()
        spacer.setContentHuggingPriority_forOrientation_(1, AppKit.NSLayoutConstraintOrientationHorizontal)
        buttons = kit.stack([self.default_button, spacer, cancel, self.save_button], vertical=False, spacing=10)

        content = kit.stack([intro, *rows, self.status, buttons], spacing=10)
        content.setCustomSpacing_afterView_(16, intro)
        content.setCustomSpacing_afterView_(4, rows[1])  # the note belongs to the combination
        content.setCustomSpacing_afterView_(16, rows[2])  # used when the status line is hidden
        content.setCustomSpacing_afterView_(16, self.status)
        view.addSubview_(content)
        kit.pin(content, view, top=_PAD, leading=_PAD, bottom=_PAD, trailing=_PAD)
        kit.constrain(buttons.widthAnchor().constraintEqualToConstant_(_TEXT_WIDTH))

    @objc.python_method
    def _wrapping(self, text: str, size: float, color: AppKit.NSColor, width: float) -> AppKit.NSTextField:
        field = kit.label(text, size, color=color, wrap=True)
        field.setPreferredMaxLayoutWidth_(width)
        return field

    @objc.python_method
    def _field(self, action: str) -> AppKit.NSButton:
        """A button that shows a key and stays pressed while it records one."""
        button = AppKit.NSButton.buttonWithTitle_target_action_("", self, action)
        button.setButtonType_(AppKit.NSButtonTypePushOnPushOff)
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        button.setTranslatesAutoresizingMaskIntoConstraints_(False)
        kit.constrain(button.widthAnchor().constraintEqualToConstant_(_FIELD_WIDTH))
        return button

    @objc.python_method
    def _row(self, title: str, control: AppKit.NSView) -> AppKit.NSStackView:
        """A right-aligned label, then ``control`` lined up with the other rows' controls."""
        name = kit.label(title, 13)
        name.setAlignment_(AppKit.NSTextAlignmentRight)
        kit.constrain(name.widthAnchor().constraintEqualToConstant_(_LABEL_WIDTH))
        return kit.stack([name, control], vertical=False, spacing=_SPACING)

    # -- Rendering --------------------------------------------------------

    @objc.python_method
    def render(self) -> None:
        model = self.model
        writable = model.load_error is None
        self.key_field.setTitle_(PRESS_KEY if self.recording == "key" else label(model.key))
        if self.recording == "combination":
            self.combination_field.setTitle_(self.capture.preview or PRESS_KEYS)
        else:
            self.combination_field.setTitle_(combination_label(model.combination))
        for field, name in ((self.key_field, "key"), (self.combination_field, "combination")):
            field.setState_(AppKit.NSControlStateValueOn if self.recording == name else AppKit.NSControlStateValueOff)
            field.setEnabled_(writable)
        self.clear_button.setHidden_(not model.combination)
        self.clear_button.setEnabled_(writable)
        self.default_button.setEnabled_(writable and not model.is_default)
        self.save_button.setEnabled_(writable)

        text, kind = (model.unreadable, "error") if not writable else model.status(self.recording, self.refusal)
        self.status.setStringValue_(text)
        self.status.setHidden_(not text)  # so an empty line leaves no gap above the buttons
        self.status.setTextColor_(_STATUS_COLORS.get(kind) or AppKit.NSColor.secondaryLabelColor())

    # -- Recording --------------------------------------------------------

    @objc.python_method
    def record(self, field: str | None) -> None:
        """Start recording in ``field``, or stop with None; the old value stays until a press is accepted."""
        self.recording = field
        self.refusal = None
        self.capture = Capture()
        self.render()

    @objc.python_method
    def took(self, keys: tuple | None) -> None:
        """What a key event completed: None while modifiers are only held, else the keys to record."""
        if keys is not None:
            record = self.model.record_key if self.recording == "key" else self.model.record_combination
            self.refusal = record(keys)
            if self.refusal is None:
                self.recording = None
            self.capture = Capture()  # keys still held can't complete a later try
        self.render()

    @objc.python_method
    def handle_key(self, event: AppKit.NSEvent) -> AppKit.NSEvent | None:
        if event.window() != self.window:
            return event
        key_down = event.type() == AppKit.NSEventTypeKeyDown
        if key_down and event.isARepeat():
            return None  # a key still held after it was recorded mustn't go on to press a button
        if self.recording is None:
            return event
        code = event.keyCode()
        if key_down:
            if code == kit.KEY_ESCAPE:
                self.record(None)
            else:
                self.took(self.capture.press(MAC_KEYS.get(code)))
        else:
            mask = _DOWN.get(code)
            if mask is None:  # Caps Lock
                self.took(self.capture.press(None))
            elif event.modifierFlags() & mask:
                self.took(self.capture.press(MAC_KEYS[code]))
            else:
                self.took(self.capture.release(MAC_KEYS[code]))
        return None

    # -- Actions ----------------------------------------------------------

    def recordKey_(self, sender) -> None:
        self.record(None if self.recording == "key" else "key")

    def recordCombination_(self, sender) -> None:
        self.record(None if self.recording == "combination" else "combination")

    def clearCombination_(self, sender) -> None:
        self.model.clear_combination()
        self.record(None)

    def useDefault_(self, sender) -> None:
        self.model.use_default()
        self.record(None)

    def save_(self, sender) -> None:
        self.record(None)
        try:
            self.model.save()
        except ValueError:
            return  # the status line shows the clash
        except (ConfigError, OSError) as e:
            log.warning("Couldn't save the hotkey: %s", e)
            kit.alert(self.window, SAVE_FAILED_TITLE, str(e))
            return
        log.info("Saved the hotkey settings")
        self.finish()

    def cancel_(self, sender) -> None:
        self.finish()

    def windowDidResignKey_(self, notification) -> None:
        if self.recording is not None:
            self.record(None)

    def windowWillClose_(self, notification) -> None:
        self.closed = True
        if self._monitor is not None:
            AppKit.NSEvent.removeMonitor_(self._monitor)
            self._monitor = None

    @objc.python_method
    def finish(self) -> None:
        self.closed = True
        self.window.close()


def run(model: HotkeyModel) -> None:
    kit.application()
    controller = HotkeyController.alloc().initWithModel_(model)
    kit.run(controller.window)
