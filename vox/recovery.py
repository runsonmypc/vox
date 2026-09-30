"""Private original recordings and durable History cleanup.

Directory manifests bridge the WAV/SQLite transaction boundary. SQLite owns metadata
once indexed; cleanup tombstones always take precedence over a manifest.
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import uuid
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

from .history import HistoryDB

RETENTION_LOCK = threading.Lock()


class RecoveryError(OSError):
    """A managed recording cannot safely be read or removed."""


def valid_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) is not None


def validate_wav(data: bytes) -> None:
    try:
        with wave.open(io.BytesIO(data), 'rb') as wav:
            size = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
            if not size or wav.getframerate() <= 0 or len(wav.readframes(wav.getnframes())) != size:
                raise ValueError('Incomplete WAV')
    except (wave.Error, EOFError, ValueError) as e:
        raise RecoveryError('Saved audio is corrupt. Delete this recording or record it again.') from e


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class RecoveryStore:
    def __init__(self, db: HistoryDB | None, path: Path | None = None) -> None:
        from .history import DEFAULT_HISTORY_PATH

        self.db = db
        self.root = (db.path if db is not None else path or DEFAULT_HISTORY_PATH).parent / 'audio'
        if self.root.is_symlink():
            raise RecoveryError('Audio storage must not be a symbolic link.')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self._lock = threading.RLock()
        self._depth = 0

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Coordinate daemon startup, reconciliation and offline deletion across processes."""
        with self._lock:
            if self._depth:
                yield
                return
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._depth += 1
                yield
            finally:
                self._depth -= 1
                os.close(fd)

    def _directory(self, audio_id: str) -> Path:
        if not valid_id(audio_id):
            raise RecoveryError('Invalid recording identifier.')
        if self.root.is_symlink():
            raise RecoveryError('Audio storage must not be a symbolic link.')
        directory = self.root / audio_id
        if directory.is_symlink():
            raise RecoveryError('Recording directory must not be a symbolic link.')
        return directory

    def _read(self, path: Path) -> bytes:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as file:
            return file.read()

    def read(self, audio_id: str) -> bytes:
        with self.locked():
            try:
                data = self._read(self._directory(audio_id) / 'original.wav')
            except OSError as e:
                raise RecoveryError('Saved audio is missing or unreadable. Delete this recording or record it again.') from e
            validate_wav(data)
            return data

    def stage(self, wav: bytes, metadata: dict) -> str:
        """Flush an unpublished recording; the caller decides whether retention is still enabled."""
        validate_wav(wav)
        audio_id = uuid.uuid4().hex
        with self.locked():
            stage = self.root / ('.staged-' + audio_id)
            stage.mkdir(mode=0o700)
            try:
                manifest = {'version': 1, 'audio_id': audio_id, **metadata}
                for name, data in (('original.wav', wav), ('manifest.json', json.dumps(manifest).encode('utf-8'))):
                    fd = os.open(stage / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, 'wb') as file:
                        file.write(data)
                        file.flush()
                        os.fsync(file.fileno())
                _sync_directory(stage)
            except BaseException:
                shutil.rmtree(stage)
                raise
        return audio_id

    def publish(self, audio_id: str, allowed: Callable[[], bool] | None = None) -> bool:
        with self.locked():
            directory = self._directory(audio_id)
            os.rename(self.root / ('.staged-' + audio_id), directory)
            try:
                _sync_directory(self.root)
            except OSError:
                # The rename happened, so keep its recoverable manifest and classify this
                # as saved/unindexed rather than creating an unrelated History row.
                raise RecoveryError('Audio publication could not be confirmed; its manifest is retained.') from None
            # Disk I/O happens before this brief privacy commit. A reload that disabled
            # retention while it ran wins; no index is exposed until this returns.
            with RETENTION_LOCK:
                accepted = allowed is None or allowed()
            if not accepted:
                shutil.rmtree(directory)
                _sync_directory(self.root)
            return accepted

    def discard_unindexed(self, audio_id: str) -> None:
        """Discard a cancelled initial write after its worker has finished publishing."""
        with self.locked():
            directory = self._directory(audio_id)
            if directory.exists():
                files = list(directory.iterdir())
                if any(p.name not in ('original.wav', 'manifest.json') or p.is_symlink() for p in files):
                    raise RecoveryError('Recording contains unsafe files.')
                for file in files:
                    file.unlink()
                directory.rmdir()
                _sync_directory(self.root)

    def discard_stage(self, audio_id: str) -> None:
        if not valid_id(audio_id):
            raise RecoveryError('Invalid recording identifier.')
        with self.locked():
            stage = self.root / ('.staged-' + audio_id)
            if stage.is_symlink():
                raise RecoveryError('Staged recording must not be a symbolic link.')
            if stage.exists():
                shutil.rmtree(stage)
                _sync_directory(self.root)

    def index(self, audio_id: str) -> int:
        if self.db is None:
            raise RecoveryError("Audio was saved but is not yet available in History.")
        with self.locked():
            directory = self._directory(audio_id)
            manifest = json.loads(self._read(directory / 'manifest.json'))
            required = {'version', 'audio_id', 'text', 'original_mode', 'attempted_mode', 'error_summary',
                        'created_at', 'duration_seconds', 'app_type'}
            if set(manifest) != required or manifest['version'] != 1 or manifest['audio_id'] != audio_id:
                raise RecoveryError('Recording metadata is invalid.')
            if manifest['original_mode'] not in ('batch', 'streaming', 'whisper_cpp') or manifest['attempted_mode'] not in (
                'batch', 'streaming', 'whisper_cpp'
            ):
                raise RecoveryError('Recording metadata is invalid.')
            if manifest['error_summary'] != 'Transcription failed. Check the selected provider and retry.':
                raise RecoveryError('Recording metadata is invalid.')
            for key in ('text', 'created_at'):
                if not isinstance(manifest[key], str):
                    raise RecoveryError('Recording metadata is invalid.')
            if manifest['app_type'] not in (None, 'TERMINAL', 'EDITOR', 'CHAT', 'EMAIL', 'BROWSER', 'OTHER'):
                raise RecoveryError('Recording metadata is invalid.')
            duration = manifest['duration_seconds']
            if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int | float)
                                         or not math.isfinite(duration) or duration < 0):
                raise RecoveryError('Recording metadata is invalid.')
            # Corrupt audio stays visible, with retry reporting the read error before a provider call.
            with self.db._lock:
                if self.db._conn.execute('SELECT 1 FROM pending_audio_cleanup WHERE audio_id = ?', (audio_id,)).fetchone():
                    raise RecoveryError('Recording is pending deletion.')
            metadata = {key: value for key, value in manifest.items() if key != 'version'}
            entry_id = self.db.insert_failure(**metadata)
            (directory / 'manifest.json').unlink()
            _sync_directory(directory)
            return entry_id

    def reconcile(self) -> list[str]:
        errors = []
        with self.locked():
            errors.extend(self.cleanup())
            for path in self.root.iterdir():
                if path.name.startswith('.staged-') and valid_id(path.name[8:]):
                    try:
                        self.discard_stage(path.name[8:])
                    except OSError:
                        errors.append('An incomplete recording could not be removed.')
                elif valid_id(path.name) and (path / 'manifest.json').exists():
                    try:
                        self.index(path.name)
                    except (OSError, sqlite3.Error, ValueError, TypeError):
                        errors.append('A saved recording could not be indexed in History.')
        return errors

    def cleanup(self) -> list[str]:
        errors = []
        with self.locked():
            with self.db._lock:
                pending = self.db._conn.execute('SELECT audio_id FROM pending_audio_cleanup').fetchall()
            for (audio_id,) in pending:
                try:
                    directory = self._directory(audio_id)
                    if directory.exists():
                        # Refuse unknown content and links, never recurse into an attacker-chosen tree.
                        children = list(directory.iterdir())
                        if any(p.name not in ('original.wav', 'manifest.json') or p.is_symlink() or not p.is_file() for p in children):
                            raise RecoveryError('Recording contains unsafe files.')
                        for file in children:
                            file.unlink()
                        directory.rmdir()
                        _sync_directory(self.root)
                    with self.db._lock, self.db._conn:
                        self.db._conn.execute('DELETE FROM pending_audio_cleanup WHERE audio_id = ?', (audio_id,))
                except OSError:
                    errors.append('Saved audio could not be removed. Cleanup will retry when Vox restarts.')
        return errors

    def delete(self, entry_id: int | None = None) -> list[str]:
        with self.locked():
            unindexed = [p.name for p in self.root.iterdir() if valid_id(p.name)] if entry_id is None else []
            with self.db._lock, self.db._conn:
                self.db._conn.execute("BEGIN IMMEDIATE")
                sql = 'SELECT audio_id FROM history WHERE audio_id IS NOT NULL'
                params = () if entry_id is None else (entry_id,)
                if entry_id is not None:
                    sql += ' AND id = ?'
                ids = [row[0] for row in self.db._conn.execute(sql, params)] + unindexed
                self.db._conn.executemany('INSERT OR IGNORE INTO pending_audio_cleanup VALUES (?)', ((i,) for i in ids))
                self.db._conn.execute('DELETE FROM history' + (' WHERE id = ?' if entry_id is not None else ''), params)
            if entry_id is None:
                for path in self.root.iterdir():
                    if path.name.startswith('.staged-') and valid_id(path.name[8:]):
                        self.discard_stage(path.name[8:])
            return self.cleanup()
