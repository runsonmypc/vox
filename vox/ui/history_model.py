"""What the history window shows, independent of toolkit: search results grouped by day, with labels."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..control import ControlClient, ControlError, ControlUnavailable
from ..history import HistoryDB, HistoryRecord

log = logging.getLogger(__name__)

_APP_NAMES = {"TERMINAL": "Terminal", "EDITOR": "Editor", "CHAT": "Chat", "EMAIL": "Email", "BROWSER": "Browser"}

# The newest results a window lists at once; searching reaches older dictations
PAGE_SIZE = 200

NO_SELECTION = ("No Selection", "Choose a dictation to read it here.")
CLEAR_BUTTON = "Clear History"


def default_copy(text: str) -> None:
    from ..injector import set_clipboard

    set_clipboard(text)


def local_datetime(created_at: str) -> datetime | None:
    """A UTC SQLite timestamp as local time, or None when it can't be parsed."""
    try:
        utc = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
    return utc.astimezone()


def day_label(day: date, today: date) -> str:
    age = (today - day).days
    if age == 0:
        return "Today"
    if age == 1:
        return "Yesterday"
    if 1 < age < 7:
        return day.strftime("%A")
    month = day.strftime("%B")
    return f"{month} {day.day}" if day.year == today.year else f"{month} {day.day}, {day.year}"


def time_label(moment: datetime, clock24: bool = False) -> str:
    return moment.strftime("%H:%M") if clock24 else moment.strftime("%I:%M %p").lstrip("0")


def app_label(app_type: str | None) -> str:
    return _APP_NAMES.get(app_type or "", "")


def duration_label(seconds: float | None) -> str:
    if not seconds or seconds < 0.5:
        return ""
    whole = round(seconds)
    return f"{whole}s" if whole < 60 else f"{whole // 60}m {whole % 60:02d}s"


def one_line(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class Entry:
    """One dictation as the list and the preview pane show it."""

    record: HistoryRecord
    preview: str  # the text on one line
    meta: str  # "2:14 PM · Terminal"
    stamp: str  # "Today at 2:14 PM"
    details: str  # "Terminal · 12s"

    @property
    def text(self) -> str:
        return self.record.text


# The list is flat: a str is a day heading, an Entry a dictation under it
Row = str | Entry


def build_rows(records: list[HistoryRecord], today: date, clock24: bool = False) -> list[Row]:
    """Records (newest first) with a heading before each new day."""
    rows: list[Row] = []
    current: str | None = None
    for rec in records:
        moment = local_datetime(rec.created_at)
        day = day_label(moment.date(), today) if moment else "Earlier"
        if day != current:
            rows.append(day)
            current = day
        time = time_label(moment, clock24) if moment else rec.created_at or ""
        app, took = app_label(rec.app_type), duration_label(rec.duration_seconds)
        status = {"failed": "Failed recording", "partial": "Partial transcript", "retrying": "Retrying…"}.get(rec.status, "")
        rows.append(Entry(
            record=rec,
            preview=(f"{status}: " if status and rec.text else "") + (one_line(rec.text) or status),
            meta=" · ".join(p for p in (time, app) if p),
            stamp=f"{day} at {time}" if moment else time,
            details=" · ".join(p for p in (app, took, status, rec.attempted_mode, rec.error_summary) if p),
        ))
    return rows


class HistoryModel:
    """Search state behind one history window."""

    def __init__(
        self,
        db: HistoryDB,
        copy: Callable[[str], None] = default_copy,
        clock24: bool = False,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._db = db
        self.control = ControlClient(db.path)
        self.control_status: dict = {}
        self.control_error: str | None = None
        self._copy = copy
        self._clock24 = clock24
        self._today = today
        self._stats: tuple[int, int | None] = (0, None)
        self._revision = -1
        self.query = ""
        self.rows: list[Row] = []
        self.truncated = False  # more matches exist than the list shows

    @property
    def entries(self) -> list[Entry]:
        return [row for row in self.rows if isinstance(row, Entry)]

    @property
    def total(self) -> int:
        """Dictations in the whole history, not just the current results."""
        return self._stats[0]

    @property
    def has_retained_audio(self) -> bool:
        root = self._db.path.parent / "audio"
        return root.exists() and any(root.iterdir())

    @property
    def placeholder(self) -> str:
        return f"Search {self.total:,} dictation{'s' if self.total != 1 else ''}" if self.total else "Search"

    @property
    def footer(self) -> str | None:
        """A note under the list when it shows only the newest results, or None."""
        if not self.truncated:
            return None
        if self.query.strip():
            return f"Showing the newest {PAGE_SIZE} matches. Refine the search to find older ones."
        return f"Showing the newest {PAGE_SIZE} dictations. Search to find older ones."

    def search(self, query: str) -> None:
        """Newest-first matches for ``query``; an empty query lists everything."""
        self.query = query
        self._revision = self._db.change_revision()
        self._stats = self._db.stats()
        found = self._db.search(query, limit=PAGE_SIZE + 1)  # one extra shows whether there are more
        self.truncated = len(found) > PAGE_SIZE
        self.rows = build_rows(found[:PAGE_SIZE], self._today(), self._clock24)

    def refresh_if_changed(self) -> bool:
        """Re-run the search when dictations were added since the last one."""
        if self._db.change_revision() == self._revision:
            return False
        self.search(self.query)
        return True

    def empty_state(self) -> tuple[str, str] | None:
        """Title and message for an empty list, or None when there are results."""
        if self.entries:
            return None
        if self.query.strip():
            return "No Results", f"Nothing matches “{self.query.strip()}”."
        return "No Dictations Yet", "Everything you dictate shows up here."

    def detail_placeholder(self) -> tuple[str, str]:
        """Title and message for the reading pane when no dictation is selected."""
        return self.empty_state() or NO_SELECTION

    def copy(self, entry: Entry) -> str | None:
        """Put the full dictation on the clipboard. Returns an error message on failure."""
        if not entry.text.strip():
            return "This recording has no transcript to copy."
        return self.copy_text(entry.text)

    def copy_text(self, text: str) -> str | None:
        """Put ``text`` on the clipboard. Returns an error message on failure."""
        try:
            self._copy(text)
        except Exception as e:
            log.warning("Copy failed: %s", e)
            return str(e) or type(e).__name__
        return None

    def neighbor_id(self, entry: Entry) -> int | None:
        """The dictation to select once ``entry`` is gone: the next one down, else the one above."""
        ids = [e.record.id for e in self.entries]
        if entry.record.id not in ids:
            return None
        i = ids.index(entry.record.id)
        if i + 1 < len(ids):
            return ids[i + 1]
        return ids[i - 1] if i > 0 else None

    def delete(self, entry: Entry) -> str | None:
        """Delete one dictation for good and search again. Returns an error message on failure."""
        try:
            self._delete_online_or_offline("delete", id=entry.record.id)
        except (sqlite3.Error, OSError, ControlError) as e:
            log.warning("Could not delete the dictation: %s", e)
            return str(e) or type(e).__name__
        log.info("Deleted a dictation from history")
        self.search(self.query)
        return None

    def poll_control(self) -> None:
        try:
            self.control_status = self.control.request("status")
            self.control_error = None
        except ControlError as e:
            self.control_status = {}
            self.control_error = str(e)

    def retry_problem(self, entry: Entry, mode: str) -> str | None:
        if entry.record.status == "completed":
            return "This dictation is already complete."
        if not entry.record.audio_id:
            return "No saved audio is available. Record this dictation again."
        return (self.control_error or self.control_status.get("disabled")
                or self.control_status.get("methods", {}).get(mode))

    def retry(self, entry: Entry, mode: str) -> tuple[str | None, str | None]:
        try:
            result = self.control.request("retry", id=entry.record.id, revision=entry.record.revision, mode=mode)
            return result["token"], None
        except ControlError as e:
            return None, str(e)

    def focus_released(self, token: str) -> str | None:
        try:
            self.control.request("ready", token=token)
        except ControlError as e:
            return str(e)
        return None

    def cancel_retry(self, entry: Entry) -> str | None:
        try:
            self.control.request("cancel", id=entry.record.id)
        except ControlError as e:
            return str(e)
        return None

    def attempts(self, entry: Entry) -> list[tuple[str, str, str]]:
        return self._db.attempts(entry.record.id)

    def _delete_online_or_offline(self, action: str, **fields: object) -> None:
        try:
            self.control.request(action, **fields)
        except ControlUnavailable:
            # Keep ownership until the mutation completes; inability to connect alone proves nothing.
            with self.control.offline():
                if action == "delete":
                    self._db.delete(fields["id"])
                else:
                    self._db.clear()

    def clear_confirmation(self) -> tuple[str, str]:
        """Title and message for the question asked before clearing the history."""
        what = "your dictation" if self.total == 1 else f"all {self.total:,} dictations"
        return "Clear History?", f"This permanently deletes {what}. You can’t undo this."

    def clear(self) -> str | None:
        """Delete every dictation for good. Returns an error message on failure."""
        try:
            self._delete_online_or_offline("clear")
        except (sqlite3.Error, OSError, ControlError) as e:
            log.warning("Could not clear the history: %s", e)
            return str(e) or type(e).__name__
        log.info("Cleared the dictation history")
        self.search(self.query)
        return None
