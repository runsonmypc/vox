"""Tests for the history search window, driven through a withdrawn Tk root."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

tk = pytest.importorskip("tkinter")

from vox.history import HistoryDB
from vox.ui.history_window import HistoryWindow, local_time, preview
from vox.ui.tkutil import create_root


@pytest.fixture
def root():
    try:
        r = create_root()
    except tk.TclError as e:
        pytest.skip(f"Tk unavailable: {e}")
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def db(tmp_path):
    history = HistoryDB(tmp_path / "history.db")
    history.insert("Deploy the Kubernetes cluster", app_type="TERMINAL")
    history.insert("Lunch at noon?", app_type="CHAT")
    history.insert("deploy script is ready\nsecond line", app_type="EDITOR")
    yield history
    history.close()


def test_opens_with_all_dictations_newest_first(root, db):
    win = HistoryWindow(root, db, copy=MagicMock())
    assert win.visible_texts() == ["deploy script is ready\nsecond line", "Lunch at noon?", "Deploy the Kubernetes cluster"]
    assert win.status.get() == "3 dictations"
    first = win.tree.get_children()[0]
    assert win.tree.selection() == (first,)
    assert win.tree.item(first, "values")[1:] == ("EDITOR", "deploy script is ready second line", "Copy")


def test_search_filters_case_insensitively(root, db):
    win = HistoryWindow(root, db, copy=MagicMock())
    win.query.set("DEPLOY")
    win.refresh()
    assert win.visible_texts() == ["deploy script is ready\nsecond line", "Deploy the Kubernetes cluster"]
    assert win.status.get() == "2 dictations"

    win.query.set("nothing matches")
    win.refresh()
    assert win.visible_texts() == []
    assert win.status.get() == "0 dictations"

    win.query.set("")
    win.refresh()
    assert len(win.visible_texts()) == 3


def test_typing_schedules_one_debounced_refresh(root, db):
    win = HistoryWindow(root, db, copy=MagicMock())
    scheduled = []

    def fake_after(ms, fn):
        scheduled.append((ms, fn))
        return f"after#{len(scheduled)}"

    with patch.object(root, "after", side_effect=fake_after), patch.object(root, "after_cancel") as cancel:
        win.query.set("lu")
        win.query.set("lunch")
    assert [ms for ms, _ in scheduled] == [120, 120]
    cancel.assert_called_once_with("after#1")  # earlier keystroke's refresh superseded
    assert len(win.visible_texts()) == 3  # nothing filtered until the timer fires

    scheduled[-1][1]()
    assert win.visible_texts() == ["Lunch at noon?"]


def test_one_click_on_copy_cell_copies_full_text(root, db):
    copy = MagicMock()
    win = HistoryWindow(root, db, copy=copy)
    target = win.tree.get_children()[0]
    with patch.object(win.tree, "identify_column", return_value="#4"), \
         patch.object(win.tree, "identify_row", return_value=target):
        win._on_click(SimpleNamespace(x=600, y=40))
    copy.assert_called_once_with("deploy script is ready\nsecond line")
    assert win.status.get() == "Copied 34 characters"


def test_click_elsewhere_does_not_copy(root, db):
    copy = MagicMock()
    win = HistoryWindow(root, db, copy=copy)
    with patch.object(win.tree, "identify_column", return_value="#3"), \
         patch.object(win.tree, "identify_row", return_value=win.tree.get_children()[1]):
        win._on_click(SimpleNamespace(x=200, y=40))
    copy.assert_not_called()


def test_copy_button_copies_selection(root, db):
    copy = MagicMock()
    win = HistoryWindow(root, db, copy=copy)
    win.tree.selection_set(win.tree.get_children()[1])
    win.copy_selected()
    copy.assert_called_once_with("Lunch at noon?")


def test_selection_shows_full_text_in_detail(root, db):
    win = HistoryWindow(root, db, copy=MagicMock())
    assert win.detail.get("1.0", "end-1c") == "deploy script is ready\nsecond line"


def test_copy_failure_is_reported_in_status(root, db):
    win = HistoryWindow(root, db, copy=MagicMock(side_effect=RuntimeError("xclip missing")))
    win.copy_selected()
    assert win.status.get() == "Copy failed: xclip missing"


def test_local_time_and_preview():
    assert local_time("not a date") == "not a date"
    assert len(local_time("2026-09-26 12:00:00")) == len("2026-09-26 12:00")
    assert preview("a\n b") == "a b"
    assert preview("x" * 200).endswith("…")


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
