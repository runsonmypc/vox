"""Hotkey tab of Settings for macOS: two fields that record keys, and Use Default.

A field records after it is clicked. While it does, the Settings window's local event
monitor hands it every key, so Return, Space and ⌘W are recorded or refused as keys, and
Esc stops recording instead of closing the window. Left and right modifiers are told apart
by the device bits of flags-changed events. Each accepted recording is saved at once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

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
    MAC_KEYS,
    PRESS_KEY,
    PRESS_KEYS,
    SAVE_FAILED_TITLE,
    USE_DEFAULT,
    Capture,
    HotkeyModel,
    combination_label,
    label,
)
from . import kit

log = logging.getLogger(__name__)

_WIDTH = 540
_PAD = 20
_TEXT_WIDTH = _WIDTH - 2 * _PAD
_LABEL_WIDTH = 130
_FIELD_WIDTH = 250
_SPACING = 8

# Key codes of the modifiers -> their NX_DEVICE*KEYMASK bit in modifierFlags, set while that key is down;
# fn, a single key, sets the function flag
_DOWN = {
    59: 0x01, 56: 0x02, 60: 0x04, 55: 0x08, 54: 0x10, 58: 0x20, 61: 0x40, 62: 0x2000,
    63: AppKit.NSEventModifierFlagFunction,
}
_STATUS_COLORS = {"error": AppKit.NSColor.systemRedColor(), "warning": AppKit.NSColor.systemOrangeColor()}


class HotkeyPage(NSObject):
    def initWithModel_alert_(self, model: HotkeyModel, alert: Callable[[str, str], None]) -> HotkeyPage:
        self = objc.super(HotkeyPage, self).init()
        if self is None:
            return None
        self.model = model
        self.alert = alert
        self.recording: str | None = None  # the field recording keys: "key" or "combination"
        self.refusal: str | None = None
        self.clash: str | None = None  # a recording refused because the combination included the hotkey
        self.capture = Capture()
        self.held: int | None = None  # the key code of a key that completed a recording and is still down
        self._build()
        return self

    # -- Layout -----------------------------------------------------------

    @objc.python_method
    def _build(self) -> None:
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

        content = kit.stack([intro, *rows, self.status, self.default_button], spacing=10)
        content.setCustomSpacing_afterView_(16, intro)
        content.setCustomSpacing_afterView_(4, rows[1])  # the note belongs to the combination
        content.setCustomSpacing_afterView_(16, rows[2])  # used when the status line is hidden
        content.setCustomSpacing_afterView_(16, self.status)
        self.view = kit.container(_WIDTH, 300)
        self.view.addSubview_(content)
        kit.constrain(
            content.topAnchor().constraintEqualToAnchor_constant_(self.view.topAnchor(), _PAD),
            content.leadingAnchor().constraintEqualToAnchor_constant_(self.view.leadingAnchor(), _PAD),
            self.view.trailingAnchor().constraintEqualToAnchor_constant_(content.trailingAnchor(), _PAD),
            self.view.bottomAnchor().constraintGreaterThanOrEqualToAnchor_constant_(content.bottomAnchor(), _PAD),
            self.view.widthAnchor().constraintGreaterThanOrEqualToConstant_(_WIDTH),
        )

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

        if not writable:
            text, kind = model.unreadable, "error"
        elif self.clash and self.recording is None:
            text, kind = self.clash, "error"
        else:
            text, kind = model.status(self.recording, self.refusal)
        self.status.setStringValue_(text)
        self.status.setHidden_(not text)  # so an empty line leaves no gap
        self.status.setTextColor_(_STATUS_COLORS.get(kind) or AppKit.NSColor.secondaryLabelColor())

    # -- Recording --------------------------------------------------------

    @objc.python_method
    def record(self, field: str | None) -> None:
        """Start recording in ``field``, or stop with None; the old value stays until a press is accepted."""
        self.recording = field
        self.refusal = None
        if field is not None:
            self.clash = None
        self.capture = Capture()
        self.render()

    @objc.python_method
    def took(self, keys: tuple | None) -> None:
        """What a key event completed: None while modifiers are only held, else the keys to record."""
        if keys is not None:
            record = self.model.record_key if self.recording == "key" else self.model.record_combination
            self.refusal = record(keys)
            self.capture = Capture()  # keys still held can't complete a later try
            if self.refusal is None:
                self.recording = None
                self.save()
        self.render()

    @objc.python_method
    def save(self) -> None:
        """Save what was just recorded, cleared or reset; a clash is refused, and both fields keep their saved values."""
        self.clash = self.model.problem
        if self.clash is not None:
            self.model.revert()
            self.render()
            return
        try:
            self.model.save()
        except (ConfigError, OSError, ValueError) as e:
            log.warning("Couldn't save the hotkey: %s", e)
            self.model.revert()
            self.alert(SAVE_FAILED_TITLE, str(e))
        else:
            log.info("Saved the hotkey settings")
        self.render()

    @objc.python_method
    def handle_key(self, event: AppKit.NSEvent) -> AppKit.NSEvent | None:
        """Key events for the Settings window. While a field records, every key is taken."""
        kind, code = event.type(), event.keyCode()
        if kind == AppKit.NSEventTypeKeyUp:
            if code == self.held:
                self.held = None
                return None
            return event if self.recording is None else None
        key_down = kind == AppKit.NSEventTypeKeyDown
        if key_down and event.isARepeat() and (self.recording is not None or code == self.held):
            return None  # a key still held after it was recorded mustn't go on to press a button
        if self.recording is None:
            return event
        if key_down:
            if code == kit.KEY_ESCAPE:
                self.record(None)
            else:
                self.took(self.capture.press(MAC_KEYS.get(code)))
                if self.recording is None:
                    self.held = code
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
        self.record(None)
        self.model.clear_combination()
        self.save()

    def useDefault_(self, sender) -> None:
        self.record(None)
        self.model.use_default()
        self.save()
