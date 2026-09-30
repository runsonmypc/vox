"""Explicit recovery jobs serialized through the daemon's processing state."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import uuid

from .control import ControlServer
from .modes import mode_problem
from .recovery import RecoveryStore
from .transcribe import PartialTranscriptionError, Transcriber
from .whisper_cpp import WhisperCppTranscriber
from .window import detect_paste_target

log = logging.getLogger(__name__)
ACK_TIMEOUT = 5


class RetryController:
    def __init__(self, daemon) -> None:
        self.daemon = daemon
        self.db = daemon.history
        self.store = RecoveryStore(self.db)
        self.server = ControlServer(self.db.path, self.handle)
        self._lock = asyncio.Lock()
        self.window_pids: set[str] = set()
        self.active_id = None
        self.token = None
        self.ready = None
        self.cancelled = False

    def disabled(self) -> str | None:
        config = self.daemon.config
        if config.config_error:
            return 'Fix the settings file before retrying.'
        if not config.keep_failed_audio:
            return 'Enable Keep failed recordings for retry in General Settings.'
        if self.daemon.hotkey_suspended:
            return 'Close Settings before retrying.'
        if self.daemon.state.value != 'IDLE':
            return 'Vox is busy. Wait for the current dictation or retry to finish.'
        return None

    async def handle(self, request: dict) -> dict:
        from .daemon import State

        self.window_pids.add(str(request['window_pid']))
        async with self._lock:
            action = request['action']
            if action == 'status':
                if self.daemon.config.openai_api_key == '':
                    await self.daemon._reload_api_key()
                return {'disabled': self.disabled(), 'active_id': self.active_id,
                        'methods': {mode: mode_problem(self.daemon.config, mode) for mode in ('batch', 'whisper_cpp')}}
            if action == 'ready':
                if self.token != request['token'] or self.ready is None or self.ready.is_set():
                    return {'error': 'Retry acknowledgement is stale. Reopen History.'}
                focus = await asyncio.to_thread(detect_paste_target, self.daemon.config)
                if not self.external(focus):
                    return {'error': 'History could not yield focus. Select an external app and try again.'}
                self.ready.set()
                return {'ok': True}
            if action in ('cancel', 'delete', 'clear'):
                if self.active_id is not None and (action == 'clear' or request['id'] == self.active_id):
                    await self.cancel()
                if action == 'delete':
                    await asyncio.to_thread(self.db.delete, request['id'])
                elif action == 'clear':
                    await asyncio.to_thread(self.db.clear)
                if self.daemon.tray is not None:
                    self.daemon.tray.history_changed()
                return {'ok': True}
            problem = self.disabled()
            if problem:
                return {'error': problem}
            rec = await asyncio.to_thread(self.db.get, request['id'])
            if rec is None or rec.revision != request['revision'] or rec.status not in ('failed', 'partial'):
                return {'error': 'This entry changed. Refresh History before retrying.'}
            if not rec.audio_id:
                return {'error': 'No saved audio is available. Record this dictation again.'}
            if request['mode'] == 'batch':
                await self.daemon._reload_api_key()
            snapshot = copy.deepcopy(self.daemon.config)
            snapshot.mode = request['mode']
            snapshot.context_screen = False
            problem = mode_problem(snapshot, snapshot.mode)
            if problem:
                return {'error': problem}
            provider = WhisperCppTranscriber(snapshot) if snapshot.mode == 'whisper_cpp' else Transcriber(snapshot)
            try:
                wav = await asyncio.to_thread(self.store.read, rec.audio_id)
            except OSError as e:
                return {'error': str(e)}
            # Settings/hotkey/config changes may have arrived while reading readiness/audio.
            problem = self.disabled()
            if problem:
                return {'error': problem}
            if not self.db.update_recovery(rec.id, rec.revision, status='retrying', attempted_mode=snapshot.mode):
                return {'error': 'This entry changed. Refresh History before retrying.'}
            self.active_id = rec.id
            self.token = uuid.uuid4().hex
            self.ready = asyncio.Event()
            self.cancelled = False
            self.daemon.set_state(State.PROCESSING)
            self.daemon.process_task = asyncio.create_task(self.run(rec.id, rec.revision + 1, snapshot, provider, wav))
            return {'token': self.token}

    def external(self, context) -> bool:
        pids = self.window_pids | {str(os.getpid())}
        if self.daemon.tray is not None:
            pids |= {str(p.pid) for p in self.daemon.tray._windows.values() if p.poll() is None}
        return bool(context.wm_class and context.pid and context.pid not in pids
                    and 'com.runsonmypc.vox' not in context.wm_class.lower())

    async def paste(self, text: str, snapshot) -> bool:
        from .injector import paste

        if self.cancelled:
            return False
        focus = await asyncio.to_thread(detect_paste_target, snapshot)
        if self.cancelled or not self.external(focus):
            if self.daemon.tray is not None:
                self.daemon.tray.set_notice('Recovered text is ready to copy in History.')
            return False
        # Await a paste already started even if cancellation arrives: clipboard restoration must finish.
        worker = asyncio.create_task(asyncio.to_thread(paste, text, focus.app_type,
                                                     eligible=lambda: not self.cancelled and self.external(detect_paste_target(snapshot))))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            await worker
            raise
        except Exception:
            self.daemon.sounds.play('error')
            if self.daemon.tray is not None:
                self.daemon.tray.set_notice('Paste failed. Copy recovered text from History.')
            return False
        return True

    async def cancel(self, expected_token: str | None = None) -> None:
        from .daemon import State

        if expected_token is not None and self.token != expected_token:
            return
        task_token = self.token
        self.cancelled = True
        entry_id = self.active_id
        task = self.daemon.process_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.token is not None and self.token != task_token:
            return
        if entry_id is not None:
            rec = self.db.get(entry_id)
            if rec is not None and rec.status == 'retrying':
                self.db.fail_retry(entry_id, rec.revision, rec.attempted_mode, 'Retry cancelled. Choose a method to try again.')
        self.active_id = self.token = self.ready = None
        self.daemon._end_processing()
        self.daemon.set_state(State.IDLE)

    async def run(self, entry_id, revision, snapshot, provider, wav):
        from .daemon import Outcome, _expand_snippet

        outcome = 'failed'
        committed = False
        try:
            await asyncio.wait_for(self.ready.wait(), ACK_TIMEOUT)
            if self.cancelled or not self.daemon.config.keep_failed_audio:
                return
            text = await provider.transcribe(wav, None)  # explicit retry has no context hints or VAD gate
            if self.cancelled or not self.daemon.config.keep_failed_audio:
                return
            if not text or not text.strip():
                self.db.fail_retry(entry_id, revision, snapshot.mode, 'Retry returned no text. Try another method.')
                revision += 1
                return
            text = _expand_snippet(text, snapshot)
            try:
                # Commit is short and synchronous, serialized with live cancellation/config gates on the loop.
                committed = self.db.finish_recovery(entry_id, revision, text, snapshot.mode)
            except Exception:
                if not self.cancelled:
                    await self.paste(text, snapshot)
                if self.daemon.tray is not None:
                    self.daemon.tray.set_notice('Recovered text could not be saved. Audio is retained for retry.')
                return
            if not committed:
                outcome = 'stale'
                return
            outcome = 'completed'
            try:
                worker = asyncio.create_task(asyncio.to_thread(self.store.cleanup))
                try:
                    errors = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    await asyncio.gather(worker, return_exceptions=True)
                    raise
            except Exception:
                errors = ['Audio cleanup could not finish. Vox will retry cleanup on restart.']
            if errors and self.daemon.tray is not None:
                self.daemon.tray.set_notice(errors[0])
            if self.daemon.tray is not None:
                self.daemon.tray.history_changed()
            await self.paste(text, snapshot)
            return Outcome.TRANSCRIBED
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        except PartialTranscriptionError as e:
            if self.cancelled:
                return
            try:
                if self.db.fail_retry(entry_id, revision, snapshot.mode, 'Retry failed partway. Try again.', e.text):
                    revision += 1
            except Exception:
                if not self.cancelled:
                    await self.paste(e.text, snapshot)
                if self.daemon.tray is not None:
                    self.daemon.tray.set_notice('Partial text could not be saved. Earlier text and audio are retained.')
        except TimeoutError:
            self.db.fail_retry(entry_id, revision, snapshot.mode, 'History did not yield focus. Try again.')
            revision += 1
        except Exception:
            self.db.fail_retry(entry_id, revision, snapshot.mode, 'Retry failed. Check the selected provider and try again.')
            revision += 1
            self.daemon.sounds.play('error')
        finally:
            try:
                rec = self.db.get(entry_id)
                if not committed and rec is not None and rec.status == "retrying" and rec.revision == revision:
                    self.db.fail_retry(entry_id, revision, snapshot.mode, 'Retry interrupted. Choose a method to try again.')
            except Exception:
                if self.daemon.tray is not None:
                    self.daemon.tray.set_notice('Retry state could not be saved. Audio and earlier text are retained.')
            finally:
                if self.daemon.tray is not None:
                    self.daemon.tray.history_changed()
                self.active_id = self.token = self.ready = None
                log.info('Recovery finished (entry_id=%s, mode=%s, outcome=%s)', entry_id, snapshot.mode, outcome)
                self.daemon.queue.put_nowait('process_done')
