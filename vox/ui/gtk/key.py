"""API key window for Linux (GTK 4 and libadwaita): a password row, where the key is kept, and Save, Remove and Cancel.

The key is typed into an ``Adw.PasswordEntryRow`` (which has its own reveal
button) and saved straight to the login keyring. "Check with OpenAI" lists
models on a background thread before saving.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from ...keystore import KeystoreError
from ..key_model import CHECK_BY_DEFAULT, KEYS_URL, CheckResult, KeyModel, Outcome, check_key
from .common import Adw, GLib, Gtk, confirm, error_dialog, label, run_app

log = logging.getLogger(__name__)

APP_ID = "com.runsonmypc.vox.ApiKey"

_INTRO = "Vox sends your dictation to OpenAI to transcribe it, using your own API key."


def _in_background(work: Callable[[], object], done: Callable[[object], None]) -> None:
    def run() -> None:
        result = work()

        def deliver() -> bool:
            done(result)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(deliver)

    threading.Thread(target=run, daemon=True).start()


class KeyWindow(Adw.ApplicationWindow):
    def __init__(self, model: KeyModel, **kwargs) -> None:
        super().__init__(title="OpenAI API Key", default_width=520, **kwargs)
        self.model = model
        self.closed = False
        self.background = _in_background  # replaced in tests
        self.confirm = confirm

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.finish())
        self.save_button = Gtk.Button(label="Save")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda _button: self.save())
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(self.save_button)

        link = Gtk.LinkButton(uri=KEYS_URL, label="Get a Key", valign=Gtk.Align.CENTER)
        key_group = Adw.PreferencesGroup(description=_INTRO, header_suffix=link)
        self.entry = Adw.PasswordEntryRow(title="API key")
        self.entry.connect("entry-activated", lambda _row: self.save())
        self.check_row = Adw.SwitchRow(title="Check with OpenAI before saving", active=CHECK_BY_DEFAULT)
        key_group.add(self.entry)
        key_group.add(self.check_row)
        self.status = label("", "caption", wrap=True)
        key_group.add(self.status)

        self.saved_row = Adw.ActionRow(use_markup=False)
        self.remove_button = Gtk.Button(label="Remove…", valign=Gtk.Align.CENTER)
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.connect("clicked", lambda _button: self.remove())
        self.saved_row.add_suffix(self.remove_button)
        self.storage = label("", "caption", "dim-label", wrap=True)
        self.override = label("", "caption", "warning", wrap=True)
        saved_group = Adw.PreferencesGroup(title="Saved Key")
        saved_group.add(self.saved_row)
        saved_group.add(self.storage)
        saved_group.add(self.override)

        self.banner = Adw.Banner()
        page = Adw.PreferencesPage()
        page.add(key_group)
        page.add(saved_group)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.set_content(view)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                         action=Gtk.CallbackAction.new(lambda *_: self.finish() or True)))
        self.add_controller(escape)
        self.connect("close-request", lambda _window: setattr(self, "closed", True) or False)
        model.reload()
        self.render()
        self.set_focus(self.entry)

    # -- Rendering ----------------------------------------------------------

    def render(self) -> None:
        model = self.model
        self.saved_row.set_title(model.saved_text)
        self.remove_button.set_visible(model.has_saved_key)
        self.storage.set_label(model.storage_text)
        self.banner.set_title(GLib.markup_escape_text("Your key won’t be encrypted. Install GNOME Keyring or KWallet to keep it safe."))
        self.banner.set_revealed(not model.encrypted)
        override = model.override_text
        self.override.set_label(override or "")
        self.override.set_visible(override is not None)
        self.set_status("")

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.set_label(text)
        self.status.set_visible(bool(text))
        if error:
            self.status.add_css_class("error")
        else:
            self.status.remove_css_class("error")

    def set_busy(self, busy: bool) -> None:
        for widget in (self.save_button, self.remove_button, self.entry, self.check_row):
            widget.set_sensitive(not busy)

    # -- Actions ------------------------------------------------------------

    def save(self) -> None:
        key = self.entry.get_text()
        problem = KeyModel.problem(key)
        if problem is not None:
            self.set_status(problem, error=True)
            return
        if not self.check_row.get_active():
            self.store(key)
            return
        self.set_busy(True)
        self.set_status("Checking with OpenAI…")
        self.background(lambda: check_key(key), lambda result: self.checked(key, result))

    def checked(self, key: str, result: CheckResult) -> None:
        self.set_busy(False)
        if result.outcome is Outcome.ACCEPTED:
            self.store(key)
        elif result.outcome is Outcome.REJECTED:
            self.set_status(result.message, error=True)
        else:
            self.set_status("")
            self.confirm(self, "Couldn’t Check the Key", f"{result.message} Save it anyway?",
                         "Save Anyway", False, lambda: self.store(key))

    def store(self, key: str) -> None:
        try:
            self.model.save(key)
        except (KeystoreError, OSError) as e:
            log.warning("Couldn't save the API key: %s", e)
            error_dialog(self, "Couldn’t Save the Key", str(e))
            return
        log.info("Saved the OpenAI API key")
        self.finish()

    def remove(self) -> None:
        self.confirm(self, "Remove the Saved Key?", "Vox can’t transcribe with OpenAI until you save a key again.",
                     "Remove", True, self.remove_key)

    def remove_key(self) -> None:
        try:
            self.model.remove()
        except (KeystoreError, OSError) as e:
            error_dialog(self, "Couldn’t Remove the Key", str(e))
            return
        log.info("Removed the OpenAI API key")
        self.finish()

    def finish(self) -> None:
        self.closed = True
        self.close()


def run(model: KeyModel) -> None:
    run_app(APP_ID, lambda app: KeyWindow(model, application=app))
