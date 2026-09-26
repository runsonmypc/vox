"""History window for Linux (GTK 4 and libadwaita): a searchable sidebar of dictations beside a reading pane.

Type anywhere to search, move with the arrow keys, and press Enter (or use the
Copy button, or a row's copy button) to put a dictation back on the clipboard.
New dictations appear while the window is open. On narrow widths the panes
collapse into one, like other GNOME apps.
"""

from __future__ import annotations

import logging

from ...history import HistoryDB
from ..history_model import Entry, HistoryModel
from .common import (
    APP_ICONS,
    DEFAULT_APP_ICON,
    Adw,
    Gdk,
    GLib,
    Gtk,
    Pango,
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
        self._toast: Adw.Toast | None = None
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
        self.connect("close-request", self._on_close)

        self._sync_activation()
        self.refresh()
        self._poll = GLib.timeout_add_seconds(_POLL_SECONDS, self._on_poll)

    # -- Layout -------------------------------------------------------------

    def _build_sidebar(self) -> Adw.NavigationPage:
        self.search = Gtk.SearchEntry(hexpand=True)
        self.search.set_key_capture_widget(self)
        self.search.connect("search-changed", lambda _entry: self.refresh())
        self.search.connect("activate", lambda _entry: self.copy_selected())
        self.search.connect("stop-search", lambda _entry: self._escape())
        arrows = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        arrows.connect("key-pressed", self._on_search_key)
        self.search.add_controller(arrows)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.list.add_css_class("navigation-sidebar")
        self.list.set_header_func(self._day_header)
        self.list.connect("row-selected", lambda _list, _row: self._show_selected())
        self.list.connect("row-activated", lambda _list, row: self._activate(row))
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.list)

        header = Adw.HeaderBar(title_widget=self.search)
        view = Adw.ToolbarView(content=self.scroller)
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

        # Selectable with the mouse, but never focused: a focused label selects all of its text
        self.text = label("", "reading", wrap=True, selectable=True, yalign=0, focusable=False)
        self.text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=560, child=self.text,
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

    def _render(self, keep_id: int | None) -> None:
        self.search.set_placeholder_text(self.model.placeholder)
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
        self._show_selected()

    def selected_entry(self) -> Entry | None:
        row = self.list.get_selected_row()
        return row.entry if row is not None else None

    def _show_selected(self) -> None:
        entry = self.selected_entry()
        self.copy_button.set_visible(entry is not None)
        if entry is None:
            title, description = self.model.empty_state() or ("No Selection", "Choose a dictation to read it here.")
            self.empty.set_icon_name("system-search-symbolic" if self.model.query.strip() else DEFAULT_APP_ICON)
            self.empty.set_title(title)
            self.empty.set_description(GLib.markup_escape_text(description))
            self.title.set_title("")
            self.title.set_subtitle("")
            self.stack.set_visible_child_name("empty")
            return
        self.title.set_title(entry.stamp)
        self.title.set_subtitle(entry.details)
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
        entry = self.selected_entry()
        if entry is not None:
            self._copy(entry)

    def _copy_row(self, row: EntryRow) -> None:
        self.list.select_row(row)
        self._copy(row.entry)

    def _copy(self, entry: Entry) -> None:
        error = self.model.copy(entry)
        if error:
            error_dialog(self, "Couldn’t Copy", error)
            return
        if self._toast is not None:
            self._toast.dismiss()
        self._toast = Adw.Toast(title="Copied to clipboard", timeout=2)
        self.toasts.add_toast(self._toast)

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
            self.move_selection(1 if keyval == Gdk.KEY_Down else -1)
            return True
        return False

    def _on_key(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        # Reached only when the focused widget left the key alone
        mods = state & Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_Escape and not mods:
            self._escape()
            return True
        if mods == Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy_selected()
            return True
        if mods == Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.search.grab_focus()
            return True
        return False

    def _on_poll(self) -> bool:
        entry = self.selected_entry()
        if self.model.refresh_if_changed():
            self._render(entry.record.id if entry else None)
        return GLib.SOURCE_CONTINUE

    def _on_close(self, _window) -> bool:
        GLib.source_remove(self._poll)
        return False


def run(db: HistoryDB) -> None:
    run_app(APP_ID, lambda app: HistoryWindow(HistoryModel(db, clock24=uses_24_hour_clock()), application=app))
