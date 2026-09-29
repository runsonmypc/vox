"""Settings window for Linux (GTK 4 and libadwaita): General, Hotkey, Transcription, Vocabulary and Snippets pages.

Built on the vocabulary window's view switcher. Every change is saved to config.toml
as it is made, with no Save button; the API key keeps its own dialog with Save. The
file is read again every 2 seconds, so a hand edit shows while the window is open.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from ..hotkey_model import HotkeyModel
from ..key_model import REMOVE_BUTTON, REMOVE_FAILED_TITLE, REMOVE_MESSAGE, REMOVE_TITLE, KeyModel
from ..settings_model import (
    CHOOSE,
    DICTATION_OFF,
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
    Choice,
    SettingsModel,
    percent,
)
from ..vocab_model import VocabModel
from .common import Adw, Gdk, Gio, GLib, Gtk, confirm, error_dialog, label, run_app
from .hotkey import HotkeyPage
from .key import KeyDialog
from .vocab import VocabWindow

log = logging.getLogger(__name__)

APP_ID = "com.runsonmypc.vox.Settings"

_POLL_SECONDS = 2


def _in_background(work: Callable[[], object], done: Callable[[object], None]) -> None:
    def run() -> None:
        result = work()
        GLib.idle_add(lambda: done(result) or GLib.SOURCE_REMOVE)

    threading.Thread(target=run, daemon=True).start()


def _choose_file(parent: Gtk.Window, current: str, then: Callable[[str], None]) -> None:
    """Ask for a file, starting next to ``current``, and call ``then`` with the path chosen."""
    dialog = Gtk.FileDialog(modal=True)
    folder = Path(current).expanduser().parent if current else None
    if folder is not None and folder.is_dir():
        dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))

    def done(dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled
        if file is not None and file.get_path():
            then(file.get_path())

    dialog.open(parent, None, done)


def _labels(choices: list[Choice]) -> Gtk.StringList:
    return Gtk.StringList.new([choice.label for choice in choices])


class _Combo:
    """An ``Adw.ComboRow`` whose rows are replaced without sending its change handler."""

    def __init__(self, row: Adw.ComboRow, changed: Callable[[int], None]) -> None:
        self.row = row
        self.filling = False
        row.connect("notify::selected", lambda *_: self.filling or changed(row.get_selected()))

    def fill(self, choices: list[Choice], selected: int) -> None:
        self.filling = True
        try:
            labels = [choice.label for choice in choices]
            model = self.row.get_model()
            if model is None or [model.get_string(i) for i in range(model.get_n_items())] != labels:
                self.row.set_model(_labels(choices))
            self.row.set_selected(selected)
        finally:
            self.filling = False


def _switch(title: str, subtitle: str, changed: Callable[[bool], None]) -> tuple[Adw.SwitchRow, list[bool]]:
    """A switch row that calls ``changed`` when the user flips it, and not when ``set`` does."""
    row = Adw.SwitchRow(title=title, subtitle=subtitle)
    filling = [False]
    row.connect("notify::active", lambda *_: filling[0] or changed(row.get_active()))
    return row, filling


def _set_switch(switch: tuple[Adw.SwitchRow, list[bool]], active: bool) -> None:
    row, filling = switch
    filling[0] = True
    try:
        row.set_active(active)
    finally:
        filling[0] = False


def _footer(page: Adw.PreferencesPage) -> Gtk.Label:
    footer = label("", "caption", "dim-label", wrap=True)
    group = Adw.PreferencesGroup()
    group.add(footer)
    page.add(group)
    return footer


class GeneralPage:
    def __init__(self, owner: SettingsWindow) -> None:
        self.owner = owner
        self.model: SettingsModel = owner.settings

        self.microphone_row = Adw.ComboRow(title=MICROPHONE)
        self.microphone = _Combo(self.microphone_row, lambda row: owner.changed(self.model.choose_microphone(row)))
        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                                         tooltip_text="Look for microphones again")
        self.refresh_button.add_css_class("flat")
        self.refresh_button.connect("clicked", lambda _button: self.refresh_devices())
        self.microphone_row.add_suffix(self.spinner)
        self.microphone_row.add_suffix(self.refresh_button)

        self.limit_row = Adw.ComboRow(title=RECORDING_LIMIT, subtitle=RECORDING_LIMIT_NOTE)
        self.limit = _Combo(self.limit_row, self.limit_changed)
        recording = Adw.PreferencesGroup()
        recording.add(self.microphone_row)
        recording.add(self.limit_row)

        self.sounds = _switch(SOUNDS, SOUNDS_NOTE, lambda on: owner.changed(self.model.set_sounds(on)))
        self.lower = _switch(LOWER_AUDIO, "", lambda on: owner.changed(self.model.set_attenuation(on)))
        self.level = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.level.set_size_request(200, -1)
        self.level.set_valign(Gtk.Align.CENTER)
        self.level.set_draw_value(False)
        self.level_text = label("", "dim-label", xalign=1, width_chars=4)
        self.level_row = Adw.ActionRow(title=LOWER_AUDIO_LEVEL)
        self.level_row.add_suffix(self.level)
        self.level_row.add_suffix(self.level_text)
        self.dragging = False
        self.filling_level = False
        self.level.connect("value-changed", lambda _scale: self.level_changed())
        pointer = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        pointer.connect("event", self.pointer_event)
        self.level.add_controller(pointer)
        sound = Adw.PreferencesGroup()
        for row in (self.sounds[0], self.lower[0], self.level_row):
            sound.add(row)

        self.screen = _switch(SCREEN_HINTS, SCREEN_HINTS_NOTE, lambda on: owner.changed(self.model.set_screen_hints(on)))
        link = Gtk.LinkButton(uri=PRIVACY_URL, label=PRIVACY_LINK, valign=Gtk.Align.CENTER)
        hints = Adw.PreferencesGroup(title="Privacy", header_suffix=link)
        hints.add(self.screen[0])

        self.view = Adw.PreferencesPage()
        for group in (recording, sound, hints):
            self.view.add(group)
        self.footer = _footer(self.view)

    def render(self) -> None:
        model, config = self.model, self.model.config
        writable = model.writable
        self.microphone.fill(*model.microphones())
        self.limit.fill(*model.limits())
        _set_switch(self.sounds, config.sounds_enabled)
        _set_switch(self.lower, config.attenuation_enabled)
        if not self.dragging:
            self.filling_level = True
            try:
                self.level.set_value(percent(config.attenuation_level))
            finally:
                self.filling_level = False
            self.level_text.set_label(f"{percent(config.attenuation_level)}%")
        _set_switch(self.screen, config.context_screen)
        for widget in (self.microphone_row, self.refresh_button, self.limit_row,
                       self.sounds[0], self.lower[0], self.screen[0]):
            widget.set_sensitive(writable)
        self.level_row.set_sensitive(writable and config.attenuation_enabled)
        self.owner.set_footer(self.footer)

    def refresh_devices(self) -> None:
        self.refresh_button.set_sensitive(False)
        self.spinner.start()

        def done(_result: object) -> None:
            self.spinner.stop()
            self.render()

        self.owner.background(self.model.refresh_devices, done)

    def limit_changed(self, row: int) -> None:
        choices, _ = self.model.limits()
        self.owner.changed(self.model.set_limit(choices[row].value))

    def pointer_event(self, _controller, event: Gdk.Event) -> bool:
        kind = event.get_event_type()
        if kind in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.TOUCH_BEGIN):
            self.dragging = True
        elif kind in (Gdk.EventType.BUTTON_RELEASE, Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
            self.dragging = False
            GLib.idle_add(lambda: self.save_level() or GLib.SOURCE_REMOVE)  # after the scale takes the release
        return False

    def level_changed(self) -> None:
        if self.filling_level:
            return
        self.level_text.set_label(f"{round(self.level.get_value())}%")
        if not self.dragging:  # the keyboard or the scroll wheel; a drag saves once, on release
            self.save_level()

    def save_level(self) -> None:
        value = self.level.get_value()
        if round(value) != percent(self.model.config.attenuation_level):
            self.owner.changed(self.model.set_attenuation_percent(value))


class TranscriptionPage:
    def __init__(self, owner: SettingsWindow) -> None:
        self.owner = owner
        self.model: SettingsModel = owner.settings
        self.key_model: KeyModel = owner.key_model
        self.dialog: KeyDialog | None = None
        self.choose_file = _choose_file  # replaced in tests
        self.filling = False

        modes = Adw.PreferencesGroup(title=MODE)
        self.mode_buttons: dict[str, Gtk.CheckButton] = {}
        self.mode_rows: dict[str, Adw.ActionRow] = {}
        self.mode_warnings: dict[str, Gtk.Image] = {}
        first = None
        for choice in self.model.modes():
            button = Gtk.CheckButton(group=first, valign=Gtk.Align.CENTER)
            first = first or button
            button.connect("toggled", lambda b, mode=choice.mode: b.get_active() and self.mode_changed(mode))
            warning = Gtk.Image(icon_name="dialog-warning-symbolic", tooltip_text="Vox Transfer can’t use this now")
            warning.add_css_class("error")
            row = Adw.ActionRow(title=choice.label, activatable_widget=button, use_markup=False)
            row.add_prefix(button)
            row.add_suffix(warning)
            self.mode_buttons[choice.mode], self.mode_rows[choice.mode] = button, row
            self.mode_warnings[choice.mode] = warning
            modes.add(row)

        self.key_row = Adw.ActionRow(title="OpenAI API key", use_markup=False)
        self.remove_key_button = Gtk.Button(label="Remove…", valign=Gtk.Align.CENTER)
        self.remove_key_button.add_css_class("flat")
        self.remove_key_button.connect("clicked", lambda _button: self.remove_key_asked())
        self.key_button = Gtk.Button(label="Set Key…", valign=Gtk.Align.CENTER)
        self.key_button.connect("clicked", lambda _button: self.open_key_dialog())
        self.key_row.add_suffix(self.remove_key_button)
        self.key_row.add_suffix(self.key_button)
        key = Adw.PreferencesGroup()
        key.add(self.key_row)

        self.language_row = Adw.ComboRow(title=LANGUAGE, enable_search=True)
        self.language = _Combo(self.language_row, lambda row: owner.changed(self.model.choose_language(row)))
        self.prompt = Adw.EntryRow(title=PROMPT, show_apply_button=True, use_markup=False)
        self.prompt.connect("apply", lambda _row: self.commit_prompt())
        self.prompt.connect("entry-activated", lambda _row: self.commit_prompt())
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _controller: self.commit_prompt())
        self.prompt.add_controller(focus)
        language = Adw.PreferencesGroup()
        language.add(self.language_row)
        prompt = Adw.PreferencesGroup(description=PROMPT_NOTE)
        prompt.add(self.prompt)

        self.binary_row = self._file_row(WHISPER_BINARY, self.choose_binary)
        self.model_row = self._file_row(WHISPER_MODEL, self.choose_model)
        local = Adw.PreferencesGroup(title="whisper.cpp")
        local.add(self.binary_row)
        local.add(self.model_row)

        self.view = Adw.PreferencesPage()
        for group in (modes, key, language, prompt, local):
            self.view.add(group)
        self.footer = _footer(self.view)

    def _file_row(self, title: str, choose: Callable[[], None]) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, use_markup=False, subtitle_selectable=True)
        button = Gtk.Button(label=CHOOSE, valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _button: choose())
        row.add_suffix(button)
        row.button = button
        return row

    def render(self, *, keep_prompt: bool = False) -> None:
        model, config = self.model, self.model.config
        writable = model.writable
        self.filling = True
        try:
            for choice in model.modes():
                button, row = self.mode_buttons[choice.mode], self.mode_rows[choice.mode]
                button.set_active(choice.mode == config.mode)
                row.set_sensitive(writable and choice.enabled)
                row.set_subtitle(GLib.markup_escape_text(choice.problem or ""))
                self.mode_warnings[choice.mode].set_visible(choice.mode == config.mode and choice.problem is not None)
        finally:
            self.filling = False
        self.language.fill(*model.languages())
        if not (keep_prompt and self.editing_prompt):
            self.prompt.set_text(config.whisper_prompt)
        self.binary_row.set_subtitle(GLib.markup_escape_text(config.whisper_cpp_binary))
        self.model_row.set_subtitle(GLib.markup_escape_text(config.whisper_cpp_model or "None chosen"))
        for widget in (self.language_row, self.prompt, self.binary_row, self.model_row):
            widget.set_sensitive(writable)

        key = self.key_model
        key.reload()
        self.key_row.set_subtitle(GLib.markup_escape_text(key.saved_text))
        if key.read_error:
            self.key_row.add_css_class("error")
        else:
            self.key_row.remove_css_class("error")
        self.key_button.set_label("Replace Key…" if key.has_saved_key else "Set Key…")
        self.remove_key_button.set_visible(key.has_saved_key)
        self.owner.set_footer(self.footer)

    @property
    def editing_prompt(self) -> bool:
        return self.prompt.has_focus() or self.prompt.get_focus_child() is not None

    def commit_prompt(self) -> None:
        """Save the prompt if it was edited: on Return, when the field is left, and when the window closes."""
        text = self.prompt.get_text()
        if text != self.model.config.whisper_prompt and self.model.writable:
            self.owner.changed(self.model.set_prompt(text))

    def mode_changed(self, mode: str) -> None:
        if not self.filling and mode != self.model.config.mode:
            self.owner.changed(self.model.set_mode(mode))

    def choose_binary(self) -> None:
        self.choose_file(self.owner, self.model.config.whisper_cpp_binary,
                         lambda path: self.owner.changed(self.model.set_whisper_binary(path)))

    def choose_model(self) -> None:
        self.choose_file(self.owner, self.model.config.whisper_cpp_model,
                         lambda path: self.owner.changed(self.model.set_whisper_model(path)))

    def open_key_dialog(self) -> None:
        if self.dialog is not None:
            return
        dialog = self.dialog = KeyDialog(self.key_model)
        dialog.on_finish = self.dialog_closed
        dialog.present(self.owner)

    def dialog_closed(self) -> None:
        self.dialog = None
        self.key_changed()

    def remove_key_asked(self) -> None:
        self.owner.confirm(self.owner, REMOVE_TITLE, REMOVE_MESSAGE, REMOVE_BUTTON, True, self.remove_key)

    def remove_key(self) -> None:
        try:
            self.key_model.remove()
        except Exception as e:
            error_dialog(self.owner, REMOVE_FAILED_TITLE, str(e))
            return
        log.info("Removed the OpenAI API key")
        self.key_changed()

    def key_changed(self) -> None:
        """A key saved or removed decides at once whether the OpenAI modes can be chosen."""
        self.model.reload_key()
        self.render(keep_prompt=True)


class SettingsWindow(VocabWindow):
    def __init__(self, settings: SettingsModel, hotkey: HotkeyModel, vocab: VocabModel, key: KeyModel,
                 **kwargs) -> None:
        # Set before the vocabulary window's init, which builds every page
        self.settings = settings
        self.hotkey_model = hotkey
        self.key_model = key
        self.background = _in_background  # replaced in tests
        self.confirm = confirm
        settings.reload()
        settings.reload_key()
        settings.refresh_devices()  # before the General page shows
        hotkey.reload()
        super().__init__(vocab, **kwargs)
        self.set_title(TITLE)
        self.set_default_size(900, 720)  # wide enough for the five page names

        off = label(DICTATION_OFF, "caption", "dim-label", xalign=0.5, margin_top=6, margin_bottom=6)
        self.view.add_bottom_bar(off)

        # Capture phase: while the Hotkey page records, keys never reach the fields or the window's shortcuts
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.hotkey.key_pressed)
        keys.connect("key-released", self.hotkey.key_released)
        self.add_controller(keys)
        self.connect("notify::is-active", lambda *_: self.is_active() or self.hotkey.focus_lost())
        self.stack.connect("notify::visible-child-name", lambda *_: self.page_shown())
        self.connect("close-request", lambda _window: self.closing() or False)

        self.render_pages()
        self.timer = GLib.timeout_add_seconds(_POLL_SECONDS, lambda: self.poll() or GLib.SOURCE_CONTINUE)

    # -- Layout -------------------------------------------------------------

    def vocab_pages(self) -> list[tuple[str, str, str, Gtk.Widget]]:
        self.general = GeneralPage(self)
        self.hotkey = HotkeyPage(self.hotkey_model, self.alert)
        self.transcription = TranscriptionPage(self)
        return [
            ("general", "General", "preferences-system-symbolic", self.general.view),
            ("hotkey", "Hotkey", "input-keyboard-symbolic", self.hotkey.view),
            ("transcription", "Transcription", "audio-input-microphone-symbolic", self.transcription.view),
            *super().vocab_pages(),
        ]

    def show_page(self, name: str) -> None:
        self.stack.set_visible_child_name(name)

    # -- Rendering ----------------------------------------------------------

    def render_pages(self, *, keep_prompt: bool = False) -> None:
        self.general.render()
        self.hotkey.render()
        self.transcription.render(keep_prompt=keep_prompt)
        self.show_banner()

    def show_banner(self) -> None:
        model = getattr(self, "settings", None)
        if model is None or model.writable:
            super().show_banner()
            return
        self.banner.set_title(GLib.markup_escape_text(model.unreadable))
        self.banner.set_revealed(True)

    def set_footer(self, footer: Gtk.Label) -> None:
        footer.set_label(f"Saved to {self.settings.shown_path} as you make each change.")

    def changed(self, error: str | None) -> None:
        """After a change: say why it couldn't be saved, if so, and show what the file holds now."""
        if error is not None:
            self.alert(SAVE_FAILED_TITLE, error)
        self.render_pages(keep_prompt=True)

    def alert(self, title: str, message: str) -> None:
        error_dialog(self, title, message)

    def poll(self) -> None:
        """Show a hand edit of config.toml, keeping a prompt being typed and a hotkey being recorded."""
        if not self.settings.poll():
            return
        self.model.reload()
        self.render()  # the Vocabulary and Snippets pages
        if self.hotkey.recording is None:
            self.hotkey_model.reload()
        self.render_pages(keep_prompt=True)

    # -- Pages and closing --------------------------------------------------

    def page_shown(self) -> None:
        if self.stack.get_visible_child_name() != "hotkey" and self.hotkey.recording is not None:
            self.hotkey.record(None)

    def closing(self) -> None:
        self.transcription.commit_prompt()
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0


def run(settings: SettingsModel, hotkey: HotkeyModel, vocab: VocabModel, key: KeyModel, page: str = "general") -> None:
    def make(app: Adw.Application) -> SettingsWindow:
        window = SettingsWindow(settings, hotkey, vocab, key, application=app)
        window.show_page(page)
        return window

    run_app(APP_ID, make)
