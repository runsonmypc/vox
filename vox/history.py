"""Local SQLite dictation history."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_HISTORY_PATH = Path.home() / ".local" / "share" / "vox" / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    text TEXT NOT NULL,
    app_type TEXT,
    duration_seconds REAL,
    transcription_mode TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_created_at ON history(created_at DESC);
"""

_COLUMNS = "id, created_at, text, app_type, duration_seconds, transcription_mode"


@dataclass(frozen=True)
class HistoryRecord:
    id: int
    created_at: str  # UTC, "YYYY-MM-DD HH:MM:SS"
    text: str
    app_type: str | None
    duration_seconds: float | None
    transcription_mode: str | None


class HistoryDB:
    """Append-only dictation log. Safe to share across threads."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_HISTORY_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def insert(
        self,
        text: str,
        app_type: str | None = None,
        duration_seconds: float | None = None,
        transcription_mode: str | None = None,
    ) -> int | None:
        """Append a dictation. Returns the row id, or None for empty text."""
        if not text or not text.strip():
            return None
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO history (text, app_type, duration_seconds, transcription_mode) VALUES (?, ?, ?, ?)",
                (text, app_type, duration_seconds, transcription_mode),
            )
        return cur.lastrowid

    def recent(self, limit: int = 3) -> list[HistoryRecord]:
        return self.search("", limit=limit)

    def search(self, query: str = "", limit: int = 200) -> list[HistoryRecord]:
        """Case-insensitive substring match on text, newest first."""
        sql = f"SELECT {_COLUMNS} FROM history"
        params: list[object] = []
        if query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql += " WHERE text LIKE ? ESCAPE '\\'"
            params.append(f"%{escaped}%")
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [HistoryRecord(*row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
