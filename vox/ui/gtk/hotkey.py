"""Hotkey page of Settings for Linux (GTK 4 and libadwaita): two rows whose buttons record keys, and Use Default.

A button records after it is clicked. While it does, the Settings window's key
controller, in the capture phase, hands this page every key before the other
controls and the window's shortcuts see it. Each accepted recording is saved at once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ...errors import ConfigError
from ..hotkey_model import (
    CLEAR_TOOLTIP,
    COMBINATION_NOTE,
    COMBINATION_ROW,
    INTRO,
    KEY_ROW,
    LINUX_KEYS,
    PRESS_KEY,
    PRESS_KEYS,
    SAVE_FAILED_TITLE,
    USE_DEFAULT,
    Capture,
    HotkeyModel,
    combination_label,
)
from ..hotkey_model import label as key_label
from .common import Adw, Gdk, Gtk, label

log = logging.getLogger(__name__)

_STATUS_CLASSES = {"error": "error", "warning": "warning", "hint": "dim-label"}


class HotkeyPage:
    def __init__(self, model: HotkeyModel, alert: Callable[[str, str], None]) -> None:
        self.model = model
        self.alert = alert
        self.recording: str | None = None  # the field recording keys: "key" or "combination"
        self.refusal: str | None = None
        self.clash: str | None = None  # a recording refused because the combination included the hotkey
        self.capture = Capture()
        self.held: int | None = None  # the hardware keycode of a key that completed a recording and is still down

        self.default_button = Gtk.Button(label=USE_DEFAULT, valign=Gtk.Align.CENTER)
        self.default_button.add_css_class("flat")
        self.default_button.connect("clicked", lambda _button: self.use_default())
        group = Adw.PreferencesGroup(description=INTRO, header_suffix=self.default_button)

        self.key_field = self._field("key")
        key_row = Adw.ActionRow(title=KEY_ROW)
        key_row.add_suffix(self.key_field)
        self.clear_button = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER,
                                       tooltip_text=CLEAR_TOOLTIP)
        self.clear_button.add_css_class("flat")
        self.clear_button.connect("clicked", lambda _button: self.clear_combination())
        self.combination_field = self._field("combination")
        combination_row = Adw.ActionRow(title=COMBINATION_ROW, subtitle=COMBINATION_NOTE)
        combination_row.add_suffix(self.clear_button)
        combination_row.add_suffix(self.combination_field)
        self.status = label("", "caption", wrap=True)
        group.add(key_row)
        group.add(combination_row)
        group.add(self.status)

        self.view = Adw.PreferencesPage()
        self.view.add(group)

    def _field(self, name: str) -> Gtk.Button:
        """A button that shows a key and records one when clicked."""
        button = Gtk.Button(valign=Gtk.Align.CENTER, width_request=180)
        button.connect("clicked", lambda _button: self.record(None if self.recording == name else name))
        return button

    # -- Rendering ----------------------------------------------------------

    def render(self) -> None:
        model = self.model
        writable = model.load_error is None
        self.key_field.set_label(PRESS_KEY if self.recording == "key" else key_label(model.key))
        if self.recording == "combination":
            self.combination_field.set_label(self.capture.preview or PRESS_KEYS)
        else:
            self.combination_field.set_label(combination_label(model.combination))
        for field, name in ((self.key_field, "key"), (self.combination_field, "combination")):
            if self.recording == name:
                field.add_css_class("suggested-action")
            else:
                field.remove_css_class("suggested-action")
        self.clear_button.set_visible(bool(model.combination))
        for widget in (self.key_field, self.combination_field, self.clear_button):
            widget.set_sensitive(writable)
        self.default_button.set_sensitive(writable and not model.is_default)

        if not writable:
            text, kind = "", None  # the window's banner says why
        elif self.clash and self.recording is None:
            text, kind = self.clash, "error"
        else:
            text, kind = model.status(self.recording, self.refusal)
        self.status.set_label(text)
        self.status.set_visible(bool(text))
        for css in _STATUS_CLASSES.values():
            self.status.remove_css_class(css)
        if kind is not None:
            self.status.add_css_class(_STATUS_CLASSES[kind])

    # -- Recording ----------------------------------------------------------

    def record(self, field: str | None) -> None:
        """Start recording in ``field``, or stop with None; the old value stays until a press is accepted."""
        self.recording = field
        self.refusal = None
        if field is not None:
            self.clash = None
        self.capture = Capture()
        self.render()

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

    def key_pressed(self, _controller, keyval: int, keycode: int, _state) -> bool:
        """A key press anywhere in the window; True takes it, so nothing else acts on it."""
        if keycode == self.held:
            return True  # a key still held after it was recorded mustn't go on to press a button
        if self.recording is None:
            return False
        name = Gdk.keyval_name(keyval)
        if name == "Escape":
            self.record(None)
        else:
            self.took(self.capture.press(LINUX_KEYS.get(name)))
            if self.recording is None:
                self.held = keycode
        return True

    def key_released(self, _controller, keyval: int, keycode: int, _state) -> None:
        if keycode == self.held:
            self.held = None
        elif self.recording is not None:
            self.took(self.capture.release(LINUX_KEYS.get(Gdk.keyval_name(keyval))))

    def focus_lost(self) -> None:
        self.held = None  # its release goes to the window that has focus now
        if self.recording is not None:
            self.record(None)

    # -- Actions ------------------------------------------------------------

    def clear_combination(self) -> None:
        self.record(None)
        self.model.clear_combination()
        self.save()

    def use_default(self) -> None:
        self.record(None)
        self.model.use_default()
        self.save()
