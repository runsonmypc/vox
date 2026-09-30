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

_RECOVERY_COLUMNS = {
    "status": "TEXT NOT NULL DEFAULT 'completed'",
    "original_mode": "TEXT",
    "attempted_mode": "TEXT",
    "audio_id": "TEXT",
    "error_summary": "TEXT",
    "revision": "INTEGER NOT NULL DEFAULT 0",
}
_COLUMNS = "id, created_at, text, app_type, duration_seconds, transcription_mode, " + ", ".join(_RECOVERY_COLUMNS)

_RECOVERY_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_history_audio_id ON history(audio_id) WHERE audio_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS history_revision (id INTEGER PRIMARY KEY CHECK (id = 1), revision INTEGER NOT NULL);
INSERT OR IGNORE INTO history_revision VALUES (1, 0);
CREATE TABLE IF NOT EXISTS partial_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    text TEXT NOT NULL,
    transcription_mode TEXT NOT NULL,
    UNIQUE(entry_id, text)
);
CREATE TABLE IF NOT EXISTS pending_audio_cleanup (audio_id TEXT PRIMARY KEY);
CREATE TRIGGER IF NOT EXISTS history_insert_revision AFTER INSERT ON history BEGIN
    UPDATE history_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER IF NOT EXISTS history_update_revision AFTER UPDATE ON history BEGIN
    UPDATE history_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER IF NOT EXISTS history_delete_revision AFTER DELETE ON history BEGIN
    UPDATE history_revision SET revision = revision + 1 WHERE id = 1;
END;
"""


@dataclass(frozen=True)
class HistoryRecord:
    id: int
    created_at: str  # UTC, "YYYY-MM-DD HH:MM:SS"
    text: str
    app_type: str | None
    duration_seconds: float | None
    transcription_mode: str | None
    status: str = "completed"
    original_mode: str | None = None
    attempted_mode: str | None = None
    audio_id: str | None = None
    error_summary: str | None = None
    revision: int = 0


class HistoryDB:
    """Dictation log. Safe to share across threads."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_HISTORY_PATH
        # Dictations are private: an owner-only file, whose mode SQLite also gives its journal files
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent == DEFAULT_HISTORY_PATH.parent:  # Vox's own data directory, made 0755 by older versions
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError as e:  # not the user's to change; the database file is still owner-only
                log.warning("Couldn't make %s owner-only: %s", self.path.parent, e)
        os.close(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600))
        os.chmod(self.path, 0o600)  # tightens a database made before this
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.create_function("casefold", 1, _casefold, deterministic=True)
        # Delete and Clear History overwrite the text with zeros in the database file. The rollback journal
        # holds a copy only while the delete runs, and is then removed rather than overwritten.
        self._conn.execute("PRAGMA secure_delete = ON")
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(history)")}
            for name, definition in _RECOVERY_COLUMNS.items():
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE history ADD COLUMN {name} {definition}")
            statement = ""
            for line in _RECOVERY_SCHEMA.splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    self._conn.execute(statement)
                    statement = ""

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

    def get(self, entry_id: int) -> HistoryRecord | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM history WHERE id = ?", (entry_id,)).fetchone()
        return HistoryRecord(*row) if row else None

    def change_revision(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT revision FROM history_revision WHERE id = 1").fetchone()[0]

    def insert_failure(
        self, *, text: str = "", audio_id: str | None = None, original_mode: str,
        attempted_mode: str, error_summary: str, created_at: str,
        duration_seconds: float | None = None, app_type: str | None = None,
    ) -> int:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            if audio_id is not None:
                if self._conn.execute("SELECT 1 FROM pending_audio_cleanup WHERE audio_id = ?", (audio_id,)).fetchone():
                    raise sqlite3.IntegrityError("Recording is pending deletion")
                row = self._conn.execute("SELECT id FROM history WHERE audio_id = ?", (audio_id,)).fetchone()
                if row:
                    return row[0]
            cur = self._conn.execute(
                "INSERT INTO history (text, audio_id, original_mode, attempted_mode, error_summary, "
                "created_at, duration_seconds, app_type, status, transcription_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (text, audio_id, original_mode, attempted_mode, error_summary, created_at,
                 duration_seconds, app_type, "partial" if text.strip() else "failed", attempted_mode if text.strip() else None),
            )
            if text.strip():
                self._conn.execute("INSERT INTO partial_attempts (entry_id, text, transcription_mode) VALUES (?, ?, ?)",
                                   (cur.lastrowid, text, attempted_mode))
            return cur.lastrowid

    def update_recovery(self, entry_id: int, revision: int, **fields: object) -> bool:
        """Update an existing revision only. A deleted/superseded result cannot recreate a row."""
        allowed = {"text", "transcription_mode", *_RECOVERY_COLUMNS} - {"revision"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid recovery update")
        if "status" in fields and fields["status"] not in ("completed", "failed", "partial", "retrying"):
            raise ValueError("Invalid recovery status")
        with self._lock, self._conn:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            cur = self._conn.execute(
                f"UPDATE history SET {assignments}, revision = revision + 1 WHERE id = ? AND revision = ?",
                (*fields.values(), entry_id, revision),
            )
            return cur.rowcount == 1

    def finish_recovery(self, entry_id: int, revision: int, text: str, mode: str) -> bool:
        if not text.strip():
            raise ValueError("Full recovery requires non-empty text")
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT audio_id FROM history WHERE id = ? AND revision = ?",
                                     (entry_id, revision)).fetchone()
            if row is None:
                return False
            if row[0]:
                self._conn.execute("INSERT OR IGNORE INTO pending_audio_cleanup VALUES (?)", (row[0],))
            self._conn.execute(
                "UPDATE history SET text = ?, transcription_mode = ?, attempted_mode = ?, status = 'completed', "
                "error_summary = NULL, audio_id = NULL, revision = revision + 1 WHERE id = ? AND revision = ?",
                (text, mode, mode, entry_id, revision),
            )
            self._conn.execute("DELETE FROM partial_attempts WHERE entry_id = ?", (entry_id,))
            return True

    def fail_retry(self, entry_id: int, revision: int, mode: str, error: str, text: str = "") -> bool:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT text FROM history WHERE id = ? AND revision = ?",
                                     (entry_id, revision)).fetchone()
            if row is None:
                return False
            preview = text if text.strip() else row[0]
            if text.strip():
                self._conn.execute("INSERT OR IGNORE INTO partial_attempts (entry_id, text, transcription_mode) VALUES (?, ?, ?)",
                                   (entry_id, text, mode))
            self._conn.execute(
                "UPDATE history SET status = ?, text = ?, attempted_mode = ?, error_summary = ?, "
                "transcription_mode = CASE WHEN ? THEN ? ELSE transcription_mode END, revision = revision + 1 "
                "WHERE id = ? AND revision = ?",
                ("partial" if preview.strip() else "failed", preview, mode, error, bool(text.strip()), mode, entry_id, revision),
            )
            return True

    def reset_interrupted_retries(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE history SET status = CASE WHEN trim(text) = '' THEN 'failed' ELSE 'partial' END, "
                "error_summary = 'Retry interrupted. Choose a method to try again.', revision = revision + 1 WHERE status = 'retrying'"
            )

    def attempts(self, entry_id: int) -> list[tuple[str, str, str]]:
        with self._lock:
            return self._conn.execute(
                "SELECT created_at, text, transcription_mode FROM partial_attempts WHERE entry_id = ? ORDER BY id",
                (entry_id,),
            ).fetchall()

    def delete(self, entry_id: int) -> None:
        """Remove one dictation."""
        from .recovery import RecoveryError, RecoveryStore

        errors = RecoveryStore(self).delete(entry_id)
        if errors:
            raise RecoveryError(errors[0])

    def clear(self) -> None:
        """Remove every dictation and all managed audio, including unindexed recordings."""
        from .recovery import RecoveryError, RecoveryStore

        errors = RecoveryStore(self).delete()
        if errors:
            raise RecoveryError(errors[0])

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
