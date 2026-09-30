"""History window for Linux (GTK 4 and libadwaita): a searchable sidebar of dictations beside a reading pane.

Type anywhere to search, move with the arrow keys, and press Enter (or use the
Copy button, or a row's copy button) to put a dictation back on the clipboard.
The trash button deletes the dictation you're reading, and the sidebar's clear
button deletes them all. New dictations appear while the window is open. On
narrow widths the panes collapse into one, like other GNOME apps.
"""

from __future__ import annotations

import logging

from ...history import HistoryDB
from ..history_model import CLEAR_BUTTON, Entry, HistoryModel
from .common import (
    APP_ICONS,
    DEFAULT_APP_ICON,
    Adw,
    Gdk,
    GLib,
    Gtk,
    Pango,
    confirm,
    error_dialog,
    label,
    run_app,
    uses_24_hour_clock,
)

log = logging.getLogger(__name__)

APP_ID = "com.runsonmypc.vox.History"
_POLL_SECONDS = 2


class EntryRow(Gtk.ListBoxRow):
    """One dictation: app icon, first line, time and app, and a copy button shown on hover."""

    def __init__(self, entry: Entry, day: str, on_copy) -> None:
        super().__init__()
        self.entry = entry
        self.day = day
        self.add_css_class("history-row")

        icon = Gtk.Image(icon_name=APP_ICONS.get(entry.record.app_type or "", DEFAULT_APP_ICON))
        icon.add_css_class("dim-label")
        text = label(entry.preview, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        meta = label(entry.meta, "caption", "dim-label", ellipsize=Pango.EllipsizeMode.END)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        column.append(text)
        column.append(meta)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Copy")
        for css in ("flat", "circular", "row-copy"):
            copy.add_css_class(css)
        copy.set_sensitive(bool(entry.text.strip()))
        copy.connect("clicked", lambda _button: on_copy(self))

        box = Gtk.Box(spacing=12)
        box.append(icon)
        box.append(column)
        box.append(copy)
        self.set_child(box)


class HistoryWindow(Adw.ApplicationWindow):
    def __init__(self, model: HistoryModel, **kwargs) -> None:
        super().__init__(title="History", default_width=920, default_height=620, **kwargs)
        self.model = model
        self.model.poll_control()
        self.confirm = confirm  # replaced in tests
        self._toast: Adw.Toast | None = None
        self._rendering = False  # rebuilding the list: row-selected fires for rows about to come back
        self._shown_id: int | None = None  # the dictation in the reading pane
        self.set_size_request(360, 400)

        self.split = Adw.NavigationSplitView(min_sidebar_width=280, max_sidebar_width=420, sidebar_width_fraction=0.38)
        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        self.split.connect("notify::collapsed", lambda *_: self._sync_activation())
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 620sp"))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        # Shortcut triggers also match on non-Latin layouts, where the key's own keyval isn't c or f
        shortcuts = Gtk.ShortcutController()  # bubble phase: a field with selected text copies it first
        shortcuts.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>c"),
                                            action=Gtk.CallbackAction.new(lambda *_: self.copy_shortcut() or True)))
        shortcuts.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>f"),
                                            action=Gtk.CallbackAction.new(lambda *_: self.search.grab_focus() or True)))
        self.add_controller(shortcuts)
        self.connect("close-request", self._on_close)

        self._sync_activation()
        self.refresh()
        self._poll = GLib.timeout_add_seconds(_POLL_SECONDS, self._on_poll)

    # -- Layout -------------------------------------------------------------

    def _build_sidebar(self) -> Adw.NavigationPage:
        self.search = Gtk.SearchEntry(hexpand=True)
        self.search.set_key_capture_widget(self)
        # Delayed while typing, so it can arrive after Enter or an arrow key already ran the search
        self.search.connect("search-changed", lambda _entry: self._sync_search())
        self.search.connect("activate", lambda _entry: self.copy_selected())
        self.search.connect("stop-search", lambda _entry: self._escape())
        arrows = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        arrows.connect("key-pressed", self._on_search_key)
        self.search.add_controller(arrows)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.list.add_css_class("navigation-sidebar")
        self.list.set_header_func(self._day_header)
        self.list.connect("row-selected", lambda _list, _row: None if self._rendering else self._show_selected())
        self.list.connect("row-activated", lambda _list, row: self._activate(row))
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.list)
        # Under the list when it shows only the newest results
        self.footer = label("", "caption", "dim-label", xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER,
                            margin_top=6, margin_bottom=10, margin_start=12, margin_end=12, visible=False)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self.scroller)
        column.append(self.footer)

        self.clear_button = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text=f"{CLEAR_BUTTON}…")
        self.clear_button.connect("clicked", lambda _button: self.clear_history())
        header = Adw.HeaderBar(title_widget=self.search)
        header.pack_start(self.clear_button)
        view = Adw.ToolbarView(content=column)
        view.add_top_bar(header)
        return Adw.NavigationPage(title="History", child=view, tag="sidebar")

    def _build_content(self) -> Adw.NavigationPage:
        self.title = Adw.WindowTitle()
        header = Adw.HeaderBar(title_widget=self.title)
        self.copy_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="edit-copy-symbolic", label="Copy"),
            tooltip_text="Copy to the clipboard (Enter)",
        )
        self.copy_button.add_css_class("suggested-action")
        self.copy_button.connect("clicked", lambda _button: self.copy_selected())
        header.pack_end(self.copy_button)
        self.delete_button = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete this dictation")
        self.delete_button.connect("clicked", lambda _button: self.delete_selected())
        header.pack_end(self.delete_button)

        # Selectable with the mouse, but never focused: a focused label selects all of its text
        self.text = label("", "reading", wrap=True, selectable=True, yalign=0, focusable=False)
        self.text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.recovery_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.recovery_note = label("", "caption", "dim-label", wrap=True)
        self.retry_local = Gtk.Button(label="Retry with Local")
        self.retry_batch = Gtk.Button(label="Retry with OpenAI Batch (uploads audio)")
        self.retry_batch.set_tooltip_text("Uploads the entire saved recording to OpenAI; another charge may apply.")
        self.cancel_retry = Gtk.Button(label="Cancel Retry")
        self.retry_local.connect("clicked", lambda _button: self.retry("whisper_cpp"))
        self.retry_batch.connect("clicked", lambda _button: self.retry("batch"))
        self.cancel_retry.connect("clicked", lambda _button: self.cancel())
        self.attempts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for widget in (self.retry_local, self.retry_batch, self.cancel_retry, self.recovery_note, self.attempts):
            self.recovery_controls.append(widget)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.append(self.recovery_controls)
        content.append(self.text)
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=560, child=content,
                          margin_top=18, margin_bottom=32, margin_start=28, margin_end=28)
        self.text_scroller = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.empty = Adw.StatusPage(icon_name=DEFAULT_APP_ICON)
        self.empty.add_css_class("compact")
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.text_scroller, "entry")
        self.stack.add_named(self.empty, "empty")

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        return Adw.NavigationPage(title="Dictation", child=view, tag="content")

    def _day_header(self, row: EntryRow, before: EntryRow | None) -> None:
        if before is None or before.day != row.day:
            row.set_header(label(row.day, "caption-heading", "dim-label", "day-header"))
        else:
            row.set_header(None)

    # -- State --------------------------------------------------------------

    def refresh(self) -> None:
        """Search for what's in the search field and select the newest match."""
        self.model.search(self.search.get_text())
        self._render(None)

    def _sync_search(self) -> None:
        if self.search.get_text() != self.model.query:
            self.refresh()

    def _render(self, keep_id: int | None) -> None:
        self.search.set_placeholder_text(self.model.placeholder)
        self.clear_button.set_sensitive(self.model.total > 0 or self.model.has_retained_audio)
        footer = self.model.footer
        self.footer.set_label(footer or "")
        self.footer.set_visible(footer is not None)
        self._rendering = True
        try:
            self.list.remove_all()
            rows: list[EntryRow] = []
            day = ""
            for item in self.model.rows:
                if isinstance(item, str):
                    day = item
                    continue
                row = EntryRow(item, day, self._copy_row)
                self.list.append(row)
                rows.append(row)
            keep = next((r for r in rows if r.entry.record.id == keep_id), None)
            self.list.select_row(keep or (rows[0] if rows else None))
        finally:
            self._rendering = False
        self._show_selected()

    def selected_entry(self) -> Entry | None:
        row = self.list.get_selected_row()
        return row.entry if row is not None else None

    def _show_selected(self) -> None:
        entry = self.selected_entry()
        self.copy_button.set_visible(entry is not None)
        self.delete_button.set_visible(entry is not None)
        if entry is None:
            self._shown_id = None
            title, description = self.model.detail_placeholder()
            self.empty.set_icon_name("system-search-symbolic" if self.model.query.strip() else DEFAULT_APP_ICON)
            self.empty.set_title(title)
            self.empty.set_description(GLib.markup_escape_text(description))
            self.title.set_title("")
            self.title.set_subtitle("")
            self.stack.set_visible_child_name("empty")
            return
        # The stamp can say "Today" or "Yesterday", so it's redrawn even when the dictation is the same
        self.title.set_title(entry.stamp)
        self.title.set_subtitle(entry.details)
        self.copy_button.set_sensitive(bool(entry.text.strip()))
        for mode, button in (("whisper_cpp", self.retry_local), ("batch", self.retry_batch)):
            problem = self.model.retry_problem(entry, mode)
            button.set_visible(entry.record.status != "completed")
            button.set_sensitive(problem is None)
            button.set_tooltip_text(problem or ("Uploads the entire recording; another charge may apply." if mode == "batch" else "Transcribe locally."))
        self.cancel_retry.set_visible(entry.record.status == "retrying")
        self.recovery_note.set_label(self.model.retry_problem(entry, "whisper_cpp") or "")
        while child := self.attempts.get_first_child():
            self.attempts.remove(child)
        for stamp, text, mode in self.model.attempts(entry):
            button = Gtk.Button(label=f"Copy partial: {stamp} · {mode}")
            button.connect("clicked", lambda _button, text=text: self._copied(self.model.copy_text(text)))
            self.attempts.append(button)
        if (entry.record.id, entry.record.revision) != self._shown_id:  # otherwise keep the reader's scroll position and selected words
            self._shown_id = (entry.record.id, entry.record.revision)
            self.text.set_label(entry.text)
            self.text.select_region(0, 0)
            self.text_scroller.get_vadjustment().set_value(0)
        self.stack.set_visible_child_name("entry")

    def move_selection(self, step: int) -> None:
        row = self.list.get_selected_row()
        target = self.list.get_row_at_index(row.get_index() + step if row else 0)
        if target is not None:
            self.list.select_row(target)
            self._scroll_to(target)

    def _scroll_to(self, row: Gtk.ListBoxRow) -> None:
        ok, bounds = row.compute_bounds(self.list)
        if not ok:
            return
        adjustment = self.scroller.get_vadjustment()
        header = row.get_header()
        top = bounds.get_y() - (header.get_height() if header is not None else 0)
        bottom = bounds.get_y() + bounds.get_height()
        if top < adjustment.get_value():
            adjustment.set_value(top)
        elif bottom > adjustment.get_value() + adjustment.get_page_size():
            adjustment.set_value(bottom - adjustment.get_page_size())

    # -- Actions ------------------------------------------------------------

    def copy_selected(self) -> None:
        self._sync_search()  # Enter right after typing copies the newest match, not the old selection
        entry = self.selected_entry()
        if entry is not None:
            self._copy(entry)

    def copy_shortcut(self) -> None:
        """Ctrl+C: the words selected in the reading pane, else the whole dictation."""
        words = self._selected_words()
        if words:
            self._copied(self.model.copy_text(words))
        else:
            self.copy_selected()

    def _selected_words(self) -> str:
        # The reading pane never takes focus, so its selection never gets Ctrl+C itself
        if self.stack.get_visible_child_name() != "entry":
            return ""
        selected, start, end = self.text.get_selection_bounds()
        return self.text.get_text()[min(start, end):max(start, end)] if selected else ""

    def _copy_row(self, row: EntryRow) -> None:
        self.list.select_row(row)
        self._copy(row.entry)

    def _copy(self, entry: Entry) -> None:
        self._copied(self.model.copy(entry))

    def _copied(self, error: str | None) -> None:
        if error:
            error_dialog(self, "Couldn’t Copy", error)
            return
        if self._toast is not None:
            self._toast.dismiss()
        self._toast = Adw.Toast(title="Copied to clipboard", timeout=2)
        self.toasts.add_toast(self._toast)

    def delete_selected(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        keep = self.model.neighbor_id(entry)
        error = self.model.delete(entry)
        if error:
            error_dialog(self, "Couldn’t Delete the Dictation", error)
            return
        self._render(keep)

    def clear_history(self) -> None:
        if self.model.total or self.model.has_retained_audio:
            title, message = self.model.clear_confirmation()
            self.confirm(self, title, message, CLEAR_BUTTON, True, self._clear)

    def _clear(self) -> None:
        error = self.model.clear()
        if error:
            error_dialog(self, "Couldn’t Clear the History", error)
            return
        self._render(None)

    def _activate(self, row: EntryRow) -> None:
        # Narrow: a tap opens the reading pane. Wide: rows select on click; double-click or Enter copies
        if self.split.get_collapsed():
            self.split.set_show_content(True)
        else:
            self._copy_row(row)

    def _sync_activation(self) -> None:
        self.list.set_activate_on_single_click(self.split.get_collapsed())

    def _escape(self) -> None:
        if self.search.get_text():
            self.search.set_text("")
        else:
            self.close()

    def _on_search_key(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        if state & Gtk.accelerator_get_default_mod_mask():
            return False
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._sync_search()  # move through the results for what's typed, not the previous search
            self.move_selection(1 if keyval == Gdk.KEY_Down else -1)
            return True
        return False

    def _on_key(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        # Reached only when the focused widget left the key alone
        mods = state & Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_Escape and not mods:
            self._escape()
            return True
        return False

    def retry(self, mode: str) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        token, error = self.model.retry(entry, mode)
        if error:
            error_dialog(self, "Couldn’t Retry", error)
            return
        self.minimize()

        def acknowledged():
            error = self.model.focus_released(token)
            if error:
                self.model.cancel_retry(entry)
                self.present()
                error_dialog(self, "Couldn’t Retry", error)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(200, acknowledged)

    def cancel(self) -> None:
        entry = self.selected_entry()
        if entry is not None:
            error = self.model.cancel_retry(entry)
            if error:
                error_dialog(self, "Couldn’t Cancel Retry", error)

    def _on_poll(self) -> bool:
        self.model.poll_control()
        self._show_selected()
        entry = self.selected_entry()
        if self.model.refresh_if_changed():
            self._render(entry.record.id if entry else None)
        return GLib.SOURCE_CONTINUE

    def _on_close(self, _window) -> bool:
        GLib.source_remove(self._poll)
        return False


def run(db: HistoryDB) -> None:
    run_app(APP_ID, lambda app: HistoryWindow(HistoryModel(db, clock24=uses_24_hour_clock()), application=app))
