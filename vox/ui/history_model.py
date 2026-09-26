"""What the history window shows, independent of toolkit: search results grouped by day, with labels."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

from ..history import HistoryDB, HistoryRecord

log = logging.getLogger(__name__)

_APP_NAMES = {"TERMINAL": "Terminal", "EDITOR": "Editor", "CHAT": "Chat", "EMAIL": "Email", "BROWSER": "Browser"}


def default_copy(text: str) -> None:
    from ..injector import set_clipboard

    set_clipboard(text)


def local_datetime(created_at: str) -> datetime | None:
    """A UTC SQLite timestamp as local time, or None when it can't be parsed."""
    try:
        utc = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
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
        rows.append(Entry(
            record=rec,
            preview=one_line(rec.text),
            meta=" · ".join(p for p in (time, app) if p),
            stamp=f"{day} at {time}" if moment else time,
            details=" · ".join(p for p in (app, took) if p),
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
        self._copy = copy
        self._clock24 = clock24
        self._today = today
        self._stats: tuple[int, int | None] = (0, None)
        self.query = ""
        self.rows: list[Row] = []

    @property
    def entries(self) -> list[Entry]:
        return [row for row in self.rows if isinstance(row, Entry)]

    @property
    def total(self) -> int:
        """Dictations in the whole history, not just the current results."""
        return self._stats[0]

    @property
    def placeholder(self) -> str:
        return f"Search {self.total:,} dictation{'s' if self.total != 1 else ''}" if self.total else "Search"

    def search(self, query: str) -> None:
        """Newest-first matches for ``query``; an empty query lists everything."""
        self.query = query
        self._stats = self._db.stats()
        self.rows = build_rows(self._db.search(query), self._today(), self._clock24)

    def refresh_if_changed(self) -> bool:
        """Re-run the search when dictations were added since the last one."""
        if self._db.stats() == self._stats:
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

    def copy(self, entry: Entry) -> str | None:
        """Put the full dictation on the clipboard. Returns an error message on failure."""
        try:
            self._copy(entry.text)
        except Exception as e:
            log.warning("Copy failed: %s", e)
            return str(e) or type(e).__name__
        return None
