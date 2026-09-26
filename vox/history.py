"""Local SQLite dictation history."""

from __future__ import annotations

import logging
import os
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
    """Dictation log. Safe to share across threads."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_HISTORY_PATH
        # Dictations are private: an owner-only file, whose mode SQLite also gives its journal files
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.close(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600))
        os.chmod(self.path, 0o600)  # tightens a database made before this
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.create_function("casefold", 1, _casefold, deterministic=True)
        # Overwrite deleted rows with zeros, so Delete and Clear History really erase the text
        self._conn.execute("PRAGMA secure_delete = ON")
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
        """Case-insensitive substring match on text, newest first. Folds case beyond ASCII, unlike LIKE."""
        sql = f"SELECT {_COLUMNS} FROM history"
        params: list[object] = []
        if query.strip():
            sql += " WHERE instr(casefold(text), ?) > 0"
            params.append(_casefold(query.strip()))
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [HistoryRecord(*row) for row in rows]

    def delete(self, entry_id: int) -> None:
        """Remove one dictation."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))

    def clear(self) -> None:
        """Remove every dictation."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM history")

    def stats(self) -> tuple[int, int | None]:
        """Number of dictations and the newest row id, a cheap way to notice new ones."""
        with self._lock:
            count, latest = self._conn.execute("SELECT COUNT(*), MAX(id) FROM history").fetchone()
        return count, latest

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _casefold(text: str | None) -> str | None:
    return text.casefold() if text is not None else None
