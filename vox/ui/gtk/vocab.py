"""Vocabulary window for Linux (GTK 4 and libadwaita): Vocabulary and Snippets pages in a view switcher.

Words are added from an entry row and removed from their row; snippets open in
a dialog to add, edit or delete. Every change is saved to config.toml at once,
and removals can be undone from the toast that confirms them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..vocab_model import (
    LOAD_FAILED_TITLE,
    NO_SNIPPETS,
    NO_WORDS,
    SAVE_FAILED_TITLE,
    SNIPPETS_INTRO,
    WORDS_INTRO,
    VocabModel,
    clash_warning,
)
from .common import Adw, GLib, Gtk, error_dialog, label, run_app

log = logging.getLogger(__name__)

APP_ID = "com.runsonmypc.vox.Vocabulary"


def _boxed_list(placeholder_title: str, placeholder_body: str) -> Gtk.ListBox:
    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    listbox.add_css_class("boxed-list")
    empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=24, margin_bottom=24,
                    margin_start=12, margin_end=12)
    empty.append(label(placeholder_title, "heading", "dim-label", xalign=0.5))
    empty.append(label(placeholder_body, "caption", "dim-label", xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER))
    listbox.set_placeholder(empty)
    return listbox


def _remove_button(tooltip: str, on_click: Callable[[], None]) -> Gtk.Button:
    button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text=tooltip)
    button.add_css_class("flat")
    button.connect("clicked", lambda _button: on_click())
    return button


class VocabWindow(Adw.ApplicationWindow):
    def __init__(self, model: VocabModel, **kwargs) -> None:
        super().__init__(title="Vocabulary", default_width=600, default_height=680, **kwargs)
        self.model = model
        self.editor: SnippetEditor | None = None
        self.set_size_request(360, 420)

        self.stack = Adw.ViewStack()
        self.stack.add_titled_with_icon(self._build_words(), "vocabulary", "Vocabulary", "accessories-dictionary-symbolic")
        self.stack.add_titled_with_icon(self._build_snippets(), "snippets", "Snippets", "insert-text-symbolic")
        self.stack.connect("notify::visible-child-name", lambda *_: self.set_title(self.stack.get_page(
            self.stack.get_visible_child()).get_title()))

        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        self.banner = Adw.Banner()
        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)

        new = Gtk.ShortcutController()  # bubble phase: fields and the snippet dialog see keys first
        new.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>n"),
                                      action=Gtk.CallbackAction.new(lambda *_: self.open_editor(None) or True)))
        new.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                      action=Gtk.CallbackAction.new(lambda *_: self.close() or True)))
        self.add_controller(new)

        model.reload()
        self.render()
        if model.load_error:
            error_dialog(self, LOAD_FAILED_TITLE, model.load_error)

    # -- Layout -------------------------------------------------------------

    def _build_words(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        adder = Adw.PreferencesGroup(description=WORDS_INTRO)
        self.word_entry = Adw.EntryRow(title="Add a word, or several separated by commas", show_apply_button=True)
        self.word_entry.set_use_markup(False)
        self.word_entry.connect("apply", lambda _row: self.add_words())
        self.word_entry.connect("entry-activated", lambda _row: self.add_words())
        adder.add(self.word_entry)
        page.add(adder)

        self.words_group = Adw.PreferencesGroup(title="Words")
        self.words_list = _boxed_list(*NO_WORDS)
        self.words_group.add(self.words_list)
        page.add(self.words_group)
        self.words_footer = self._footer(page)
        return page

    def _build_snippets(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        self.new_button = Gtk.Button(child=Adw.ButtonContent(icon_name="list-add-symbolic", label="New Snippet"),
                                     valign=Gtk.Align.CENTER)
        self.new_button.add_css_class("flat")
        self.new_button.connect("clicked", lambda _button: self.open_editor(None))
        group = Adw.PreferencesGroup(description=SNIPPETS_INTRO, header_suffix=self.new_button)
        self.snippets_list = _boxed_list(*NO_SNIPPETS)
        group.add(self.snippets_list)
        page.add(group)
        self.snippets_footer = self._footer(page)
        return page

    def _footer(self, page: Adw.PreferencesPage) -> Gtk.Label:
        footer = label("", "caption", "dim-label", wrap=True)
        group = Adw.PreferencesGroup()
        group.add(footer)
        page.add(group)
        return footer

    # -- Rendering ----------------------------------------------------------

    def render(self) -> None:
        self.words_list.remove_all()
        for word in self.model.words:
            row = Adw.ActionRow(title=word, use_markup=False)
            row.add_suffix(_remove_button("Remove", lambda w=word: self.remove_word(w)))
            self.words_list.append(row)
        self.words_group.set_title(f"Words · {len(self.model.words)}" if self.model.words else "Words")

        self.snippets_list.remove_all()
        for trigger, expansion in self.model.snippets.items():
            row = Adw.ActionRow(title=trigger, subtitle=" ".join(expansion.split()), use_markup=False,
                                subtitle_lines=1, activatable=True)
            row.add_suffix(_remove_button("Delete", lambda t=trigger: self.remove_snippet(t)))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _row, t=trigger: self.open_editor(t))
            self.snippets_list.append(row)

        broken = self.model.load_error is not None
        self.banner.set_title(GLib.markup_escape_text(f"Couldn’t read {self.model.shown_path}. Fix the file, then reopen this window."))
        self.banner.set_revealed(broken)
        for widget in (self.word_entry, self.new_button, self.words_list, self.snippets_list):
            widget.set_sensitive(not broken)
        footer = f"Saved to {self.model.shown_path}. Vox picks up changes within a few seconds."
        self.words_footer.set_label(footer)
        self.snippets_footer.set_label(footer)

    # -- Changes ------------------------------------------------------------

    def change(self, write: Callable[[], object]) -> bool:
        """Run a config write and redraw. Returns False after showing the error."""
        try:
            write()
        except Exception as e:
            log.warning("Config update failed: %s", e)
            error_dialog(self.editor or self, SAVE_FAILED_TITLE, str(e))
            return False
        self.render()
        return True

    def toast(self, title: str, undo: Callable[[], object] | None = None) -> None:
        toast = Adw.Toast(title=GLib.markup_escape_text(title), timeout=4 if undo else 2)
        if undo is not None:
            toast.set_button_label("Undo")
            toast.connect("button-clicked", lambda _toast: self.change(undo))
        self.toasts.add_toast(toast)

    def add_words(self) -> None:
        text = self.word_entry.get_text()
        if not text.strip():
            return
        added: list[str] = []
        if not self.change(lambda: added.extend(self.model.add_words(text))):
            return
        self.word_entry.set_text("")
        self.word_entry.grab_focus()
        if added:
            self.toast(f"Added “{added[0]}”" if len(added) == 1 else f"Added {len(added)} words")
        else:
            self.toast("Already in your vocabulary")

    def remove_word(self, word: str) -> None:
        if self.change(lambda: self.model.remove_words([word])):
            self.toast(f"Removed “{word}”", undo=lambda: self.model.add_words(word))

    def remove_snippet(self, trigger: str) -> None:
        expansion = self.model.snippets.get(trigger, "")
        if self.change(lambda: self.model.remove_snippet(trigger)):
            self.toast(f"Deleted “{trigger}”", undo=lambda: self.model.save_snippet(trigger, expansion))

    def save_snippet(self, trigger: str, expansion: str, original: str | None) -> bool:
        if not self.change(lambda: self.model.save_snippet(trigger, expansion, original)):
            return False
        self.toast(f"Saved “{trigger.strip()}”")
        return True

    def open_editor(self, original: str | None) -> None:
        if self.editor is not None:
            return
        self.stack.set_visible_child_name("snippets")
        self.editor = SnippetEditor(self, original)
        self.editor.connect("closed", lambda _dialog: setattr(self, "editor", None))
        self.editor.present(self)


class SnippetEditor(Adw.Dialog):
    """Dialog for adding or editing one snippet. Ctrl+Enter saves."""

    def __init__(self, owner: VocabWindow, original: str | None) -> None:
        super().__init__(title="New Snippet" if original is None else "Edit Snippet", content_width=480)
        self.owner = owner
        self.original = original

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.close())
        self.save_button = Gtk.Button(label="Save")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda _button: self.save())
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(self.save_button)

        self.trigger = Adw.EntryRow(title="When you say", use_markup=False)
        self.trigger.connect("changed", lambda _row: self.validate())
        self.trigger.connect("entry-activated", lambda _row: self._next())
        trigger_group = Adw.PreferencesGroup()
        trigger_group.add(self.trigger)

        self.expansion = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                      top_margin=10, bottom_margin=10, left_margin=12, right_margin=12)
        self.expansion.add_css_class("snippet-text")
        self.expansion.get_buffer().connect("changed", lambda _buffer: self.validate())
        frame = Gtk.ScrolledWindow(child=self.expansion, min_content_height=140, hscrollbar_policy=Gtk.PolicyType.NEVER)
        frame.add_css_class("card")
        frame.add_css_class("snippet-box")
        expansion_group = Adw.PreferencesGroup(title="Vox types")
        expansion_group.add(frame)

        self.warning = label("", "caption", "warning", wrap=True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=24,
                       margin_start=18, margin_end=18)
        for widget in (trigger_group, expansion_group, self.warning):
            body.append(widget)
        if original is not None:
            delete = Gtk.Button(label="Delete Snippet", halign=Gtk.Align.CENTER)
            delete.add_css_class("destructive-action")
            delete.add_css_class("pill")
            delete.connect("clicked", lambda _button: self._delete())
            body.append(delete)
            self.trigger.set_text(original)
            self.expansion.get_buffer().set_text(owner.model.snippets.get(original, ""))

        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.set_child(view)

        save_keys = Gtk.ShortcutController()
        save_keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>Return"),
                                            action=Gtk.CallbackAction.new(lambda *_: self.save() or True)))
        self.add_controller(save_keys)
        self.set_focus(self.trigger if original is None else self.expansion)
        self.validate()

    def expansion_text(self) -> str:
        buffer = self.expansion.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def validate(self) -> None:
        trigger = self.trigger.get_text()
        self.save_button.set_sensitive(bool(trigger.strip() and self.expansion_text().strip()))
        clash = self.owner.model.conflict(trigger, self.original)
        self.warning.set_label(clash_warning(clash) if clash else "")
        self.warning.set_visible(bool(clash))

    def save(self) -> None:
        if self.save_button.get_sensitive() and self.owner.save_snippet(
            self.trigger.get_text(), self.expansion_text(), self.original
        ):
            self.close()

    def _next(self) -> None:
        # Enter in the trigger moves on to an empty expansion, and saves otherwise
        if self.expansion_text().strip():
            self.save()
        else:
            self.expansion.grab_focus()

    def _delete(self) -> None:
        self.close()
        self.owner.remove_snippet(self.original)


def run(model: VocabModel) -> None:
    run_app(APP_ID, lambda app: VocabWindow(model, application=app))
