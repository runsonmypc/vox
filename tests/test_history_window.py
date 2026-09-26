"""Tests for the history window: the shared model, then the macOS and Linux views built on it."""

import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vox.history import HistoryDB, HistoryRecord
from vox.ui.history_model import (
    Entry,
    HistoryModel,
    build_rows,
    day_label,
    duration_label,
    local_datetime,
    time_label,
)

# Local noon today, so "hours ago" never crosses midnight whenever the tests run
NOON = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)


def add(db: HistoryDB, text: str, days: int = 0, hours: int = 0, app: str | None = "TERMINAL",
        duration: float | None = 4.2) -> None:
    at = (NOON - timedelta(days=days, hours=hours)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            "INSERT INTO history (created_at, text, app_type, duration_seconds) VALUES (?, ?, ?, ?)",
            (at, text, app, duration),
        )


@pytest.fixture
def db(tmp_path):
    history = HistoryDB(tmp_path / "history.db")
    add(history, "Deploy the Kubernetes cluster", days=1, hours=2, app="TERMINAL")
    add(history, "Lunch at noon?", hours=3, app="CHAT", duration=2.0)
    add(history, "deploy script is ready\nsecond line", hours=1, app="EDITOR", duration=75)
    yield history
    history.close()


def model_for(db, copy=None, **kwargs):
    model = HistoryModel(db, copy=copy or MagicMock(), today=lambda: NOON.date(), **kwargs)
    model.search("")
    return model


def texts(model):
    return [entry.text for entry in model.entries]


# -- Model ----------------------------------------------------------------------


def test_lists_everything_newest_first_under_day_headings(db):
    model = model_for(db)
    assert [r if isinstance(r, str) else r.text for r in model.rows] == [
        "Today", "deploy script is ready\nsecond line", "Lunch at noon?",
        "Yesterday", "Deploy the Kubernetes cluster",
    ]
    first = model.entries[0]
    assert first.preview == "deploy script is ready second line"
    assert first.meta == f"{time_label(NOON - timedelta(hours=1))} · Editor"
    assert first.stamp == f"Today at {time_label(NOON - timedelta(hours=1))}"
    assert first.details == "Editor · 1m 15s"


def test_search_is_case_insensitive_and_reports_why_it_is_empty(db):
    model = model_for(db)
    model.search("DEPLOY")
    assert texts(model) == ["deploy script is ready\nsecond line", "Deploy the Kubernetes cluster"]
    assert model.empty_state() is None

    model.search("nothing matches")
    assert model.rows == []
    assert model.empty_state() == ("No Results", "Nothing matches “nothing matches”.")


def test_empty_history(tmp_path):
    db = HistoryDB(tmp_path / "empty.db")
    model = model_for(db)
    assert model.empty_state() == ("No Dictations Yet", "Everything you dictate shows up here.")
    assert model.placeholder == "Search"
    db.close()


def test_placeholder_counts_the_whole_history_not_the_results(db):
    model = model_for(db)
    model.search("lunch")
    assert model.placeholder == "Search 3 dictations"


def test_refresh_if_changed_picks_up_new_dictations(db):
    model = model_for(db)
    model.search("deploy")
    assert model.refresh_if_changed() is False
    add(db, "deploy again", hours=0)
    assert model.refresh_if_changed() is True
    assert texts(model)[0] == "deploy again"
    assert model.query == "deploy"


def test_copy_puts_the_full_text_on_the_clipboard(db):
    copy = MagicMock()
    model = model_for(db, copy=copy)
    assert model.copy(model.entries[0]) is None
    copy.assert_called_once_with("deploy script is ready\nsecond line")


def test_copy_failure_returns_a_message(db):
    model = model_for(db, copy=MagicMock(side_effect=RuntimeError("xclip missing")))
    assert model.copy(model.entries[0]) == "xclip missing"


def test_labels():
    today = date(2026, 9, 26)  # a Saturday
    assert day_label(today, today) == "Today"
    assert day_label(date(2026, 9, 25), today) == "Yesterday"
    assert day_label(date(2026, 9, 21), today) == "Monday"
    assert day_label(date(2026, 9, 12), today) == "September 12"
    assert day_label(date(2025, 12, 31), today) == "December 31, 2025"

    moment = datetime(2026, 9, 26, 14, 5)
    assert time_label(moment) == "2:05 PM"
    assert time_label(moment, clock24=True) == "14:05"
    assert duration_label(None) == duration_label(0.2) == ""
    assert duration_label(8.4) == "8s"
    assert duration_label(125) == "2m 05s"
    assert local_datetime("not a date") is None


def test_unparseable_timestamps_still_list():
    rec = HistoryRecord(1, "garbage", "hello", None, None, None)
    rows = build_rows([rec], date(2026, 9, 26))
    assert rows[0] == "Earlier"
    assert isinstance(rows[1], Entry) and rows[1].meta == "garbage" and rows[1].details == ""


# -- macOS --------------------------------------------------------------------------


def _key(AppKit, window, chars, code, flags=0):
    return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        AppKit.NSEventTypeKeyDown, (0, 0), flags, 0, window.windowNumber(), None, chars, chars, False, code
    )


@pytest.fixture
def mac_window(appkit, db):
    from vox.ui.mac.history import HistoryController

    copy = MagicMock()
    controller = HistoryController.alloc().initWithModel_(model_for(db, copy=copy))
    controller.copied = copy
    yield controller
    controller.window.close()


def test_mac_opens_on_the_newest_dictation(appkit, mac_window):
    assert mac_window.table.numberOfRows() == 5
    assert mac_window.table.selectedRow() == 1  # row 0 is the "Today" heading
    assert mac_window.text_view.string() == "deploy script is ready\nsecond line"
    assert mac_window.stamp.stringValue().startswith("Today at ")
    assert mac_window.search.placeholderString() == "Search 3 dictations"
    assert mac_window.empty.isHidden()


def test_mac_day_headings_are_group_rows_that_cannot_be_selected(appkit, mac_window):
    table = mac_window.table
    assert mac_window.tableView_isGroupRow_(table, 0) and mac_window.tableView_isGroupRow_(table, 3)
    assert not mac_window.tableView_shouldSelectRow_(table, 3)
    assert mac_window.tableView_shouldSelectRow_(table, 4)


def test_mac_arrow_keys_skip_headings(appkit, mac_window):
    mac_window.move_selection(1)
    assert mac_window.selected_entry().text == "Lunch at noon?"
    mac_window.move_selection(1)
    assert mac_window.selected_entry().text == "Deploy the Kubernetes cluster"
    mac_window.move_selection(1)  # already last
    assert mac_window.table.selectedRow() == 4
    mac_window.move_selection(-1)
    assert mac_window.selected_entry().text == "Lunch at noon?"


def test_mac_search_and_empty_state(appkit, mac_window):
    mac_window.search.setStringValue_("zebra")
    mac_window.searchChanged_(mac_window.search)
    assert mac_window.table.numberOfRows() == 0
    assert mac_window.content.isHidden() and not mac_window.empty.isHidden()
    assert mac_window.empty_title.stringValue() == "No Results"


def test_mac_return_copies_and_escape_clears_then_closes(appkit, mac_window):
    window = mac_window.window
    assert mac_window.handle_key(_key(appkit, window, "\r", 36)) is None
    mac_window.copied.assert_called_once_with("deploy script is ready\nsecond line")
    assert mac_window.copy_button.title() == "Copied"

    mac_window.search.setStringValue_("lunch")
    mac_window.refresh()
    assert mac_window.handle_key(_key(appkit, window, "\x1b", 53)) is None
    assert mac_window.search.stringValue() == "" and mac_window.table.numberOfRows() == 5

    mac_window.handle_key(_key(appkit, window, "\x1b", 53))
    assert not mac_window._timer.isValid()  # windowWillClose_ ran


def test_mac_row_copy_button_copies_that_row(appkit, mac_window):
    table = mac_window.table
    cell = table.viewAtColumn_row_makeIfNecessary_(0, 4, True)
    mac_window.copyRow_(cell.accessory)
    mac_window.copied.assert_called_once_with("Deploy the Kubernetes cluster")
    assert table.selectedRow() == 4


def test_mac_new_dictations_appear_and_keep_the_selection(appkit, db, mac_window):
    mac_window.move_selection(1)
    add(db, "brand new", hours=0)
    mac_window.poll_(None)
    assert mac_window.table.numberOfRows() == 6
    assert mac_window.selected_entry().text == "Lunch at noon?"


# -- Linux --------------------------------------------------------------------------


def _drain(gtk):
    context = gtk.GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


@pytest.fixture
def gtk_window(gtk, db):
    from vox.ui.gtk.history import HistoryWindow

    copy = MagicMock()
    window = HistoryWindow(model_for(db, copy=copy))
    window.copied = copy
    yield window
    window.destroy()
    _drain(gtk)


def test_gtk_opens_on_the_newest_dictation(gtk, gtk_window):
    rows = [gtk_window.list.get_row_at_index(i) for i in range(3)]
    assert [r.entry.text for r in rows] == [
        "deploy script is ready\nsecond line", "Lunch at noon?", "Deploy the Kubernetes cluster",
    ]
    assert [r.day for r in rows] == ["Today", "Today", "Yesterday"]
    assert gtk_window.list.get_selected_row() is rows[0]
    assert gtk_window.text.get_label() == "deploy script is ready\nsecond line"
    assert gtk_window.title.get_title().startswith("Today at ")
    assert gtk_window.search.get_placeholder_text() == "Search 3 dictations"


def test_gtk_search_and_empty_state(gtk, gtk_window):
    gtk_window.search.set_text("zebra")
    gtk_window.refresh()
    assert gtk_window.list.get_row_at_index(0) is None
    assert gtk_window.stack.get_visible_child_name() == "empty"
    assert gtk_window.empty.get_title() == "No Results"
    assert not gtk_window.copy_button.get_visible()


def test_gtk_arrows_move_and_enter_copies(gtk, gtk_window):
    gtk_window.move_selection(1)
    assert gtk_window.selected_entry().text == "Lunch at noon?"
    gtk_window.search.emit("activate")
    gtk_window.copied.assert_called_once_with("Lunch at noon?")


def test_gtk_row_copy_button_copies_that_row(gtk, gtk_window):
    row = gtk_window.list.get_row_at_index(2)
    gtk_window._copy_row(row)
    gtk_window.copied.assert_called_once_with("Deploy the Kubernetes cluster")
    assert gtk_window.list.get_selected_row() is row


def test_gtk_escape_clears_the_search_then_closes(gtk, gtk_window):
    gtk_window.search.set_text("lunch")
    gtk_window._escape()
    assert gtk_window.search.get_text() == ""
    with patch.object(gtk_window, "close") as close:
        gtk_window._escape()
    close.assert_called_once()


def test_gtk_new_dictations_appear_and_keep_the_selection(gtk, db, gtk_window):
    gtk_window.move_selection(1)
    add(db, "brand new", hours=0)
    gtk_window._on_poll()
    assert gtk_window.list.get_row_at_index(3) is not None
    assert gtk_window.selected_entry().text == "Lunch at noon?"


# -- Launch -------------------------------------------------------------------------


def test_main_runs_the_platform_window_and_closes_the_db(tmp_path):
    from vox.ui import history_window

    module = "vox.ui.mac.history" if sys.platform == "darwin" else "vox.ui.gtk.history"
    fake = SimpleNamespace(run=MagicMock())
    with patch.dict(sys.modules, {module: fake}):
        history_window.main(["--db", str(tmp_path / "h.db")])
    (db,), _ = fake.run.call_args
    assert db.path == tmp_path / "h.db"
    with pytest.raises(sqlite3.ProgrammingError):
        db.search("")


@pytest.mark.skipif(sys.platform != "darwin", reason="NSPasteboard is macOS-only")
def test_set_clipboard_writes_nspasteboard():
    """Exercise the real AppKit write path against a private pasteboard, not the user's clipboard."""
    import AppKit

    from vox.injector import set_clipboard

    private = AppKit.NSPasteboard.pasteboardWithUniqueName()
    try:
        with patch("AppKit.NSPasteboard", SimpleNamespace(generalPasteboard=lambda: private)):
            set_clipboard("recovered dictation ✓")
        assert private.stringForType_(AppKit.NSPasteboardTypeString) == "recovered dictation ✓"
    finally:
        private.releaseGlobally()
