"""Recovery through fake providers, with real temporary History and original audio."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_recovery import metadata, wav_bytes

from vox.config import Config
from vox.control import ControlError, ControlServer, identity, validate
from vox.daemon import State, _ConfigApplier, _process
from vox.history import HistoryDB
from vox.recovery import RecoveryStore
from vox.retry import RetryController
from vox.transcribe import PartialTranscriptionError
from vox.window import AppContext, AppType

EXTERNAL = AppContext('editor', '', AppType.EDITOR, pid='999999')


@pytest.fixture
def recovery(tmp_path):
    db = HistoryDB(tmp_path / 'history.db')
    store = RecoveryStore(db)
    audio_id = store.stage(wav_bytes(), metadata('prior'))
    store.publish(audio_id)
    entry_id = store.index(audio_id)
    daemon = SimpleNamespace(history=db, config=Config(), hotkey_suspended=False, state=State.IDLE,
                             tray=None, process_task=None, sounds=MagicMock(), queue=asyncio.Queue())
    daemon._reload_api_key = AsyncMock()
    daemon.set_state = lambda state: setattr(daemon, 'state', state)
    daemon._end_processing = lambda: setattr(daemon, 'process_task', None)
    controller = RetryController(daemon)
    daemon.retry = controller
    yield controller, daemon, entry_id, audio_id
    db.close()


def request(controller, action, **fields):
    return dict(action=action, database=identity(controller.db.path), window_pid=os.getpid(), **fields)


async def begin(controller, entry_id, mode='whisper_cpp'):
    provider = SimpleNamespace(transcribe=AsyncMock(return_value='complete'))
    with patch('vox.retry.mode_problem', return_value=None), patch('vox.retry.WhisperCppTranscriber', return_value=provider), \
         patch('vox.retry.Transcriber', return_value=provider):
        result = await controller.handle(request(controller, 'retry', id=entry_id,
                                                 revision=controller.db.get(entry_id).revision, mode=mode))
    assert 'token' in result, result
    return result['token'], provider


async def ready(controller, token):
    with patch('vox.retry.detect_paste_target', return_value=EXTERNAL):
        assert await controller.handle(request(controller, 'ready', token=token)) == {'ok': True}


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['whisper_cpp', 'batch'])
async def test_retry_full_recording_selected_mode_after_restart(recovery, mode):
    controller, daemon, entry_id, audio_id = recovery
    daemon.config.mode = 'streaming'
    daemon.config.snippets = {'complete': 'expanded'}
    token, provider = await begin(controller, entry_id, mode)
    provider.transcribe.assert_not_awaited()
    with patch('vox.retry.detect_paste_target', return_value=EXTERNAL), patch('vox.injector.paste') as paste:
        await ready(controller, token)
        await daemon.process_task
    provider.transcribe.assert_awaited_once_with(wav_bytes(), None)
    paste.assert_called_once()
    assert paste.call_args.args == ('expanded', AppType.EDITOR)
    rec = controller.db.get(entry_id)
    assert rec.status == 'completed' and rec.text == 'expanded' and rec.transcription_mode == mode
    assert rec.created_at == metadata()['created_at'] and rec.duration_seconds == 0.1
    assert controller.db.attempts(entry_id) == [] and len(controller.db.search()) == 1
    assert not (controller.store.root / audio_id).exists()
    assert daemon.config.mode == 'streaming'


@pytest.mark.anyio
async def test_busy_off_config_settings_duplicate_stale_and_no_ack(recovery):
    controller, daemon, entry_id, _ = recovery
    for attr, value in [('keep_failed_audio', False), ('config_error', 'invalid')]:
        setattr(daemon.config, attr, value)
        assert 'error' in await controller.handle(request(controller, 'retry', id=entry_id, revision=0, mode='batch'))
        setattr(daemon.config, attr, True if attr == 'keep_failed_audio' else None)
    daemon.hotkey_suspended = True
    assert controller.disabled()
    daemon.hotkey_suspended = False
    token, provider = await begin(controller, entry_id)
    assert 'error' in await controller.handle(request(controller, 'retry', id=entry_id, revision=0, mode='batch'))
    provider.transcribe.assert_not_awaited()
    with patch('vox.retry.detect_paste_target', return_value=AppContext('vox', '', AppType.OTHER, pid=str(os.getpid()))):
        assert 'error' in await controller.handle(request(controller, 'ready', token=token))
    await controller.cancel()
    assert controller.db.get(entry_id).status == 'partial'
    provider.transcribe.assert_not_awaited()
    assert daemon.state is State.IDLE


@pytest.mark.anyio
@pytest.mark.parametrize('result', ['', RuntimeError('secret dictated words'), PartialTranscriptionError('secret', 'new partial')])
async def test_retry_failure_keeps_audio_and_distinct_partial_attempts(recovery, result):
    controller, daemon, entry_id, audio_id = recovery
    token, provider = await begin(controller, entry_id)
    if isinstance(result, Exception):
        provider.transcribe.side_effect = result
    else:
        provider.transcribe.return_value = result
    with patch('vox.retry.detect_paste_target', return_value=EXTERNAL), patch('vox.injector.paste') as paste:
        await ready(controller, token)
        await daemon.process_task
    paste.assert_not_called()
    rec = controller.db.get(entry_id)
    assert rec.status == 'partial' and 'secret' not in rec.error_summary
    assert controller.store.read(audio_id) == wav_bytes()
    assert rec.text == ('new partial' if isinstance(result, PartialTranscriptionError) else 'prior')
    assert len(controller.db.attempts(entry_id)) == (2 if isinstance(result, PartialTranscriptionError) else 1)


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['cancel', 'delete', 'clear'])
async def test_cancel_and_delete_during_provider_prevent_late_paste(recovery, action):
    controller, daemon, entry_id, audio_id = recovery
    token, provider = await begin(controller, entry_id)
    started = asyncio.Event()

    async def long_call(*args):
        started.set()
        await asyncio.Event().wait()
    provider.transcribe.side_effect = long_call
    with patch('vox.retry.detect_paste_target', return_value=EXTERNAL), patch('vox.injector.paste') as paste:
        await ready(controller, token)
        await started.wait()
        fields = {} if action == 'clear' else {'id': entry_id}
        await controller.handle(request(controller, action, **fields))
    paste.assert_not_called()
    assert daemon.state is State.IDLE
    if action == 'cancel':
        assert controller.db.get(entry_id).status == 'partial'
        assert controller.store.read(audio_id) == wav_bytes()
    else:
        assert controller.db.get(entry_id) is None
        assert not (controller.store.root / audio_id).exists()


@pytest.mark.anyio
async def test_reopened_history_and_paste_error_preserve_completed_text(recovery):
    controller, daemon, entry_id, audio_id = recovery
    token, _ = await begin(controller, entry_id)
    await ready(controller, token)
    with patch('vox.retry.detect_paste_target', return_value=AppContext('vox', '', AppType.OTHER, pid=str(os.getpid()))), \
         patch('vox.injector.paste') as paste:
        await daemon.process_task
    paste.assert_not_called()
    assert controller.db.get(entry_id).text == 'complete'
    assert not (controller.store.root / audio_id).exists()


@pytest.mark.anyio
async def test_commit_failure_pastes_and_keeps_original_audio(recovery):
    controller, daemon, entry_id, audio_id = recovery
    token, _ = await begin(controller, entry_id)
    await ready(controller, token)
    with patch.object(controller.db, 'finish_recovery', side_effect=OSError('readonly')), \
         patch('vox.retry.detect_paste_target', return_value=EXTERNAL), patch('vox.injector.paste') as paste:
        await daemon.process_task
    paste.assert_called_once()
    assert controller.db.get(entry_id).text == 'prior'
    assert controller.store.read(audio_id) == wav_bytes()
    assert controller.active_id is None


@pytest.mark.anyio
async def test_capture_failure_restart_reconcile_and_disabled_retention(tmp_path):
    db = HistoryDB(tmp_path / 'history.db')
    provider = SimpleNamespace(transcribe=AsyncMock(side_effect=PartialTranscriptionError('secret', 'prior')))
    cfg = Config()
    kwargs = dict(wav_data=wav_bytes(), config=cfg, batch_transcriber=provider, streaming_transcriber=None,
                  stream_task=None, sounds=MagicMock(), queue=asyncio.Queue(), context=EXTERNAL,
                  screen_capture_future=None, mode='batch', history=db)
    with patch('vox.daemon.has_speech', return_value=True), patch('vox.daemon.paste') as paste:
        await _process(**kwargs)
    paste.assert_not_called()
    rec = db.search()[0]
    assert rec.status == 'partial' and rec.audio_id
    path = db.path
    db.close()
    db = HistoryDB(path)
    db.reset_interrupted_retries()
    assert RecoveryStore(db).reconcile() == []
    assert len(db.search()) == 1
    assert RecoveryStore(db).read(rec.audio_id) == wav_bytes()
    db.clear()
    cfg.keep_failed_audio = False
    kwargs['history'] = db
    with patch('vox.daemon.has_speech', return_value=True), patch('vox.daemon.paste'):
        await _process(**kwargs)
    rec = db.search()[0]
    assert rec.status == 'completed' and rec.text == 'prior' and rec.audio_id is None
    assert list((tmp_path / 'audio').iterdir()) == []
    db.close()


@pytest.mark.parametrize('fields', [dict(action='retry', id=-1, revision=0, mode='batch'),
                                   dict(action='retry', id=1, revision=False, mode='batch'),
                                   dict(action='retry', id=1, revision=0, mode='streaming'),
                                   dict(action='ready', token='../audio'), dict(action='bogus')])
def test_control_validation(tmp_path, fields):
    db = tmp_path / 'history.db'
    with pytest.raises(ControlError):
        validate(dict(database=identity(db), window_pid=os.getpid(), **fields), db)
    with pytest.raises(ControlError, match='same database'):
        validate(dict(database='wrong', window_pid=os.getpid(), action='status'), db)


@pytest.mark.anyio
async def test_socket_bounds_identity_and_permissions(tmp_path):
    server = ControlServer(tmp_path / 'history.db', AsyncMock(return_value={'ok': True}), __import__('vox.control', fromlist=['endpoint']).endpoint())
    await server.start()
    try:
        import stat
        assert stat.S_IMODE(server.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(server.path.parent.stat().st_mode) == 0o700
        for line in (b'not json\n', b'{}\n', b'x' * 5000 + b'\n'):
            reader, writer = await asyncio.open_unix_connection(server.path)
            writer.write(line)
            await writer.drain()
            assert b'error' in await reader.readline()
            writer.close()
            await writer.wait_closed()
        server.handle.assert_not_awaited()
    finally:
        await server.close()
    assert not server.path.exists()


@pytest.mark.anyio
async def test_missing_ack_times_out_without_provider_call(recovery, monkeypatch):
    controller, daemon, entry_id, audio_id = recovery
    monkeypatch.setattr('vox.retry.ACK_TIMEOUT', 0.01)
    _token, provider = await begin(controller, entry_id)
    await daemon.process_task
    provider.transcribe.assert_not_awaited()
    assert controller.db.get(entry_id).status == 'partial'
    assert controller.store.read(audio_id) == wav_bytes()


@pytest.mark.anyio
async def test_stale_completion_never_pastes_or_recreates_deleted_row(recovery):
    controller, daemon, entry_id, _ = recovery
    token, provider = await begin(controller, entry_id)

    async def deletes_during_call(*args):
        controller.db.delete(entry_id)
        return 'late result'
    provider.transcribe.side_effect = deletes_during_call
    with patch('vox.retry.detect_paste_target', return_value=EXTERNAL), patch('vox.injector.paste') as paste:
        await ready(controller, token)
        await daemon.process_task
    paste.assert_not_called()
    assert controller.db.get(entry_id) is None


@pytest.mark.anyio
async def test_cleanup_database_failure_does_not_suppress_completed_paste(recovery):
    controller, daemon, entry_id, _ = recovery
    token, _ = await begin(controller, entry_id)
    with patch('vox.retry.detect_paste_target', return_value=EXTERNAL), patch('vox.injector.paste') as paste, \
         patch.object(controller.store, 'cleanup', side_effect=OSError('readonly')):
        await ready(controller, token)
        await daemon.process_task
    paste.assert_called_once()
    assert controller.db.get(entry_id).status == 'completed'


@pytest.mark.anyio
async def test_paste_error_preserves_completed_text(recovery):
    controller, daemon, entry_id, audio_id = recovery
    token, _ = await begin(controller, entry_id)
    with patch('vox.retry.detect_paste_target', return_value=EXTERNAL), patch('vox.injector.paste', side_effect=OSError('paste')):
        await ready(controller, token)
        await daemon.process_task
    assert controller.db.get(entry_id).status == 'completed'
    assert not (controller.store.root / audio_id).exists()
    daemon.sounds.play.assert_called_with('error')


@pytest.mark.anyio
@pytest.mark.parametrize('phase', ['stage', 'publish'])
async def test_disabling_during_retention_prevents_new_audio(tmp_path, phase):
    import threading

    from vox.daemon import _retain_failure

    db = HistoryDB(tmp_path / 'history.db')
    config = Config()
    start, release = threading.Event(), threading.Event()
    original = getattr(RecoveryStore, phase)

    def slow(self, *args, **kwargs):
        start.set()
        release.wait(5)
        return original(self, *args, **kwargs)

    with patch.object(RecoveryStore, phase, slow):
        task = asyncio.create_task(_retain_failure(wav_bytes(), config, db, EXTERNAL, 'batch', 'batch', '', None))
        await asyncio.to_thread(start.wait, 5)
        _ConfigApplier(config, MagicMock())(Config(keep_failed_audio=False))
        release.set()
        assert await task is False
    assert db.search() == [] and list((tmp_path / 'audio').iterdir()) == []
    db.close()


@pytest.mark.anyio
async def test_initial_retention_index_fault_has_one_entry_after_reconcile(tmp_path):
    from vox.daemon import _retain_failure

    db = HistoryDB(tmp_path / 'history.db')
    with patch.object(db, 'insert_failure', side_effect=OSError('readonly')):
        assert await _retain_failure(wav_bytes(), Config(), db, EXTERNAL, 'batch', 'batch', 'part', None) is None
    assert db.search() == []
    assert RecoveryStore(db).reconcile() == []
    assert len(db.search()) == 1 and db.search()[0].text == 'part'
    db.close()


@pytest.mark.anyio
async def test_restart_resets_interrupted_retry_without_provider_or_paste(recovery):
    controller, daemon, entry_id, audio_id = recovery
    controller.db.update_recovery(entry_id, 0, status='retrying')
    controller.db.reset_interrupted_retries()
    assert controller.db.get(entry_id).status == 'partial'
    assert controller.store.read(audio_id) == wav_bytes()
    assert daemon.process_task is None


@pytest.mark.anyio
async def test_cancel_after_commit_before_paste_keeps_completed_text(recovery):
    import threading

    controller, daemon, entry_id, audio_id = recovery
    token, _ = await begin(controller, entry_id)
    started, released = threading.Event(), threading.Event()
    original = controller.store.cleanup

    def delayed_cleanup():
        started.set()
        released.wait(5)
        return original()

    with patch('vox.retry.detect_paste_target', return_value=EXTERNAL), patch('vox.injector.paste') as paste, \
         patch.object(controller.store, 'cleanup', side_effect=delayed_cleanup):
        await ready(controller, token)
        await asyncio.to_thread(started.wait, 5)
        assert controller.db.get(entry_id).status == 'completed'
        cancelled = asyncio.create_task(controller.cancel())
        await asyncio.sleep(0)
        released.set()
        await cancelled
    paste.assert_not_called()
    assert controller.db.get(entry_id).status == 'completed'
    assert not (controller.store.root / audio_id).exists()


@pytest.mark.anyio
async def test_cancel_after_paste_starts_waits_for_clipboard_restoration(recovery):
    import threading

    controller, daemon, entry_id, _ = recovery
    token, _ = await begin(controller, entry_id)
    started, released, restored = threading.Event(), threading.Event(), threading.Event()

    def delivering(*args, **kwargs):
        assert kwargs['eligible']()
        started.set()  # fake paste keystroke already sent
        released.wait(5)
        restored.set()

    with patch('vox.retry.detect_paste_target', return_value=EXTERNAL), patch('vox.injector.paste', side_effect=delivering):
        await ready(controller, token)
        await asyncio.to_thread(started.wait, 5)
        cancelled = asyncio.create_task(controller.cancel())
        await asyncio.sleep(0)
        assert not cancelled.done()
        released.set()
        await cancelled
    assert restored.is_set()
    assert controller.db.get(entry_id).status == 'completed'


@pytest.mark.anyio
async def test_valid_hot_reload_cancels_retry_and_reenable_restores_admission(recovery):
    controller, daemon, entry_id, audio_id = recovery
    _token, _provider = await begin(controller, entry_id)
    applier = _ConfigApplier(daemon.config, MagicMock(), recovery_changed=lambda: setattr(controller, 'cancelled',
                                                                                        not daemon.config.keep_failed_audio))
    applier(Config(keep_failed_audio=False))
    await controller.cancel()
    assert controller.disabled() and controller.store.read(audio_id) == wav_bytes()
    applier(Config(keep_failed_audio=True))
    assert controller.disabled() is None


def test_offline_deletion_requires_daemon_lock(tmp_path):
    from vox.ui.history_model import HistoryModel

    db = HistoryDB(tmp_path / 'history.db')
    entry_id = db.insert('keep this')
    model = HistoryModel(db)
    model.search('')
    with model.control.offline():
        assert model.delete(model.entries[0]) is not None
        assert db.get(entry_id) is not None
    assert model.delete(model.entries[0]) is None
    assert db.get(entry_id) is None
    db.close()


@pytest.mark.anyio
@pytest.mark.parametrize('phase', ['stage', 'publish'])
async def test_cancelled_storage_worker_failure_does_not_retain_or_index(tmp_path, phase):
    import threading

    from vox.daemon import _retain_failure

    db = HistoryDB(tmp_path / 'history.db')
    started, released = threading.Event(), threading.Event()

    def failing(self, *args):
        started.set()
        released.wait(5)
        raise OSError('disk full')

    with patch.object(RecoveryStore, phase, failing):
        task = asyncio.create_task(_retain_failure(wav_bytes(), Config(), db, EXTERNAL, 'batch', 'batch', '', None))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert db.search() == []
    assert list((tmp_path / 'audio').iterdir()) == []
    db.close()


@pytest.mark.anyio
async def test_disabled_publication_sync_failure_discards_uncommitted_directory(tmp_path):
    import threading

    from vox.daemon import _retain_failure

    db = HistoryDB(tmp_path / 'history.db')
    config = Config()
    original = RecoveryStore.publish
    started, released = threading.Event(), threading.Event()

    def failing_publish(self, audio_id, allowed):
        started.set()
        released.wait(5)
        with patch('vox.recovery._sync_directory', side_effect=OSError('disk full')):
            return original(self, audio_id, allowed)

    with patch.object(RecoveryStore, 'publish', failing_publish):
        task = asyncio.create_task(_retain_failure(wav_bytes(), config, db, EXTERNAL, 'batch', 'batch', '', None))
        await asyncio.to_thread(started.wait, 5)
        _ConfigApplier(config, MagicMock())(Config(keep_failed_audio=False))
        released.set()
        assert await task is False
    assert db.search() == [] and list((tmp_path / 'audio').iterdir()) == []
    db.close()
