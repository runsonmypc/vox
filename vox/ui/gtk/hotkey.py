"""Hotkey window for Linux (GTK 4 and libadwaita): two rows whose buttons record keys, Use Default, Cancel and Save.

A button records after it is clicked. While it does, a key controller in the
capture phase takes every key before the buttons and the window's Escape shortcut see it.
"""

from __future__ import annotations

import logging

from ...errors import ConfigError
from ..hotkey_model import (
    CLEAR_TOOLTIP,
    COMBINATION_NOTE,
    COMBINATION_ROW,
    INTRO,
    KEY_ROW,
    LINUX_KEYS,
    LOAD_FAILED_TITLE,
    PRESS_KEY,
    PRESS_KEYS,
    SAVE_FAILED_TITLE,
    TITLE,
    USE_DEFAULT,
    Capture,
    HotkeyModel,
    combination_label,
)
from ..hotkey_model import label as key_label
from .common import Adw, Gdk, GLib, Gtk, error_dialog, label, run_app

log = logging.getLogger(__name__)

APP_ID = "com.runsonmypc.vox.Hotkey"

_STATUS_CLASSES = {"error": "error", "warning": "warning", "hint": "dim-label"}


class HotkeyWindow(Adw.ApplicationWindow):
    def __init__(self, model: HotkeyModel, **kwargs) -> None:
        super().__init__(title=TITLE, default_width=480, **kwargs)
        self.model = model
        self.closed = False
        self.recording: str | None = None  # the field recording keys: "key" or "combination"
        self.refusal: str | None = None
        self.capture = Capture()
        self.down: set[int] = set()  # hardware keycodes held down, to drop auto-repeated presses

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.finish())
        self.save_button = Gtk.Button(label="Save")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda _button: self.save())
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(self.save_button)

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

        self.banner = Adw.Banner()
        page = Adw.PreferencesPage()
        page.add(group)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.set_content(view)

        # Capture phase: while a field records, keys never reach the buttons or the Escape shortcut below
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.key_pressed)
        keys.connect("key-released", self.key_released)
        self.add_controller(keys)
        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                         action=Gtk.CallbackAction.new(lambda *_: self.finish() or True)))
        self.add_controller(escape)
        self.connect("notify::is-active", lambda *_: self.is_active() or self.focus_lost())
        self.connect("close-request", lambda _window: setattr(self, "closed", True) or False)

        model.reload()
        self.render()
        if model.load_error:
            error_dialog(self, LOAD_FAILED_TITLE, model.load_error)

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
        for widget in (self.key_field, self.combination_field, self.clear_button, self.save_button):
            widget.set_sensitive(writable)
        self.default_button.set_sensitive(writable and not model.is_default)
        self.banner.set_title(GLib.markup_escape_text(model.unreadable))
        self.banner.set_revealed(not writable)

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
        self.capture = Capture()
        self.render()

    def took(self, keys: tuple | None) -> None:
        """What a key event completed: None while modifiers are only held, else the keys to record."""
        if keys is not None:
            record = self.model.record_key if self.recording == "key" else self.model.record_combination
            self.refusal = record(keys)
            if self.refusal is None:
                self.recording = None
            self.capture = Capture()  # keys still held can't complete a later try
        self.render()

    def key_pressed(self, _controller, keyval: int, keycode: int, _state) -> bool:
        if keycode in self.down:
            return True  # a key still held after it was recorded mustn't go on to press a button
        self.down.add(keycode)
        if self.recording is None:
            return False
        name = Gdk.keyval_name(keyval)
        if name == "Escape":
            self.record(None)
        else:
            self.took(self.capture.press(LINUX_KEYS.get(name)))
        return True

    def key_released(self, _controller, keyval: int, keycode: int, _state) -> None:
        self.down.discard(keycode)
        if self.recording is not None:
            self.took(self.capture.release(LINUX_KEYS.get(Gdk.keyval_name(keyval))))

    def focus_lost(self) -> None:
        self.down.clear()  # their releases go to the window that has focus now
        if self.recording is not None:
            self.record(None)

    # -- Actions ------------------------------------------------------------

    def clear_combination(self) -> None:
        self.model.clear_combination()
        self.record(None)

    def use_default(self) -> None:
        self.model.use_default()
        self.record(None)

    def save(self) -> None:
        self.record(None)
        try:
            self.model.save()
        except ValueError:
            return  # the status line shows the clash
        except (ConfigError, OSError) as e:
            log.warning("Couldn't save the hotkey: %s", e)
            error_dialog(self, SAVE_FAILED_TITLE, str(e))
            return
        log.info("Saved the hotkey settings")
        self.finish()

    def finish(self) -> None:
        self.closed = True
        self.close()


def run(model: HotkeyModel) -> None:
    run_app(APP_ID, lambda app: HotkeyWindow(model, application=app))
