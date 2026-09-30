"""Bounded owner-only control channel between History and the daemon."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path

from .__main__ import _acquire_instance_lock, _lock_dir
from .recovery import RecoveryError

MAX_REQUEST = 4096
TIMEOUT = 5


class ControlError(ValueError):
    pass


class ControlUnavailable(ControlError):
    pass


def endpoint() -> Path:
    return _lock_dir() / 'history.sock'


def identity(path: Path) -> str:
    return str(path.resolve())


def validate(request: object, db: Path) -> dict:
    if not isinstance(request, dict) or request.get('database') != identity(db):
        raise ControlError('History and Vox must use the same database and audio store.')
    action = request.get('action')
    fields = {
        'status': set(), 'retry': {'id', 'revision', 'mode'}, 'ready': {'token'},
        'cancel': {'id'}, 'delete': {'id'}, 'clear': set(),
    }
    if action not in fields or set(request) != {'database', 'action', 'window_pid', *fields[action]}:
        raise ControlError('Invalid History request.')
    if type(request['window_pid']) is not int or request['window_pid'] <= 0:
        raise ControlError('Invalid History window identity.')
    if 'id' in request and (type(request['id']) is not int or request['id'] <= 0):
        raise ControlError('Choose a valid History entry.')
    if 'revision' in request and (type(request['revision']) is not int or request['revision'] < 0):
        raise ControlError('Refresh History before retrying.')
    if action == 'retry' and request['mode'] not in ('batch', 'whisper_cpp'):
        raise ControlError('Choose Local or OpenAI Batch.')
    if action == 'ready' and (not isinstance(request['token'], str) or len(request['token']) != 32
                              or any(c not in '0123456789abcdef' for c in request['token'])):
        raise ControlError('Invalid retry acknowledgement.')
    return request


class ControlServer:
    def __init__(self, db: Path, handle: Callable[[dict], Awaitable[dict]], path: Path | None = None) -> None:
        self.db, self.handle, self.path = db, handle, path or endpoint()
        self.server = None
        self._inode = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ControlError('The Vox runtime directory must not be a symbolic link.')
        os.chmod(self.path.parent, 0o700)
        if self.path.exists():
            if not stat.S_ISSOCK(self.path.stat().st_mode):
                raise ControlError('The Vox control endpoint is occupied by another file.')
            self.path.unlink()  # daemon startup already owns the single-instance directory lock
        self.server = await asyncio.start_unix_server(self._connection, path=self.path, limit=MAX_REQUEST + 1)
        os.chmod(self.path, 0o600)
        self._inode = self.path.stat().st_ino

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            line = await asyncio.wait_for(reader.readline(), TIMEOUT)
            if not line.endswith(b'\n') or len(line) > MAX_REQUEST:
                raise ControlError('History request is too large or incomplete.')
            request = validate(json.loads(line), self.db)
            response = await self.handle(request)
        except (ControlError, RecoveryError) as e:
            response = {"error": str(e)}
        except (TimeoutError, ValueError, OSError):
            response = {'error': 'History request failed. Refresh History and check that Vox is running.'}
        except Exception:
            response = {'error': 'History storage or provider setup is unavailable. Check Settings and try again.'}
        try:
            writer.write(json.dumps(response).encode() + b'\n')
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self._tasks.discard(task)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.path.exists() and self.path.stat().st_ino == self._inode:
            self.path.unlink()


class ControlClient:
    def __init__(self, db: Path, path: Path | None = None) -> None:
        self.db, self.path = db, path or endpoint()

    def request(self, action: str, **fields: object) -> dict:
        request = {'database': identity(self.db), 'action': action, 'window_pid': os.getpid(), **fields}
        validate(request, self.db)
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(TIMEOUT)
                sock.connect(str(self.path))
                sock.sendall(json.dumps(request).encode() + b'\n')
                data = b''
                while not data.endswith(b'\n'):
                    chunk = sock.recv(MAX_REQUEST)
                    if not chunk or len(data) + len(chunk) > MAX_REQUEST:
                        raise ControlError('Vox returned an incomplete response. Reopen History.')
                    data += chunk
            response = json.loads(data)
        except (OSError, ValueError) as e:
            if isinstance(e, ControlError):
                raise
            raise ControlUnavailable('Cannot connect to Vox. Start Vox or reopen History; no retry was started.') from e
        if 'error' in response:
            raise ControlError(response['error'])
        return response

    @contextlib.contextmanager
    def offline(self):
        """Hold the daemon ownership lock throughout an offline deletion."""
        fd = _acquire_instance_lock(_lock_dir())
        if fd is None:
            raise ControlError('Vox is running but its control connection is unavailable. Reopen History before deleting.')
        try:
            yield
        finally:
            os.close(fd)
