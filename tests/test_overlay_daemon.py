"""Use the actual daemon lifecycle with controlled audio and provider boundaries."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from test_overlay import rig as rig
from test_pipeline_daemon import SILENT, SPEECH, openai_config, running, until

from vox.config import Config, load_config
from vox.daemon import CANCELLED_NOTICE, State, _config_reloader
from vox.errors import AudioError
from vox.history import HistoryDB
from vox.recovery import CANCELLED_SUMMARY, RecoveryStore
from vox.ui.history_model import build_rows
from vox.window import start_screen_capture


@pytest.mark.anyio
@pytest.mark.parametrize('existing_panel', [False, True])
async def test_startup_capture_shows_once_before_ocr_finishes(rig, existing_panel):
    entered, screenshot, recognize = [threading.Event() for _ in range(3)]
    if existing_panel:
        rig.start(0)
        rig.flush()
        rig.overlay.dismiss()
        rig.flush()

    def capture(_win_id, _pid, _app_type, guard):
        entered.set()
        assert screenshot.wait(2)
        with guard() as hidden:
            assert hidden
            assert not any(backend.visible for backend in rig.made)
        assert recognize.wait(2)
        return 'screen words'

    with ThreadPoolExecutor(max_workers=1) as pool, \
         patch('vox.window._ocr_pool', pool), \
         patch('vox.window._capture_screen_text', capture), \
         patch('vox.daemon.create_overlay', return_value=rig.overlay):
        async with running(openai_config(attenuation_enabled=False)) as h:
            h.recorder.latest_level = None
            h.capture.side_effect = start_screen_capture
            try:
                await h.daemon._start_recording()
                await until(entered.is_set)
                rig.flush()
                h.recorder.start.assert_called_once()
                assert rig.overlay.snapshot.phase == 'listening'
                assert not any(backend.visible for backend in rig.made)
                if not existing_panel:
                    assert not rig.made  # includes the delay before the screenshot guard is entered
                screenshot.set()

                def capture_ready():
                    rig.flush()  # acknowledge the worker's hide when reusing a native panel
                    return not rig.overlay._captures

                await until(capture_ready)
                rig.flush()
                backend = rig.made[0]
                assert backend.visible
                assert not h.daemon.session.screen_future.done()  # OCR is still running
                backend.hide = Mock(wraps=backend.hide)
                recognize.set()
                await h.daemon.session.screen_future
                rig.flush()
                backend.hide.assert_not_called()
                assert backend.visible
            finally:
                screenshot.set()
                recognize.set()


@pytest.mark.anyio
async def test_capture_submission_failure_releases_startup_suppression(rig):
    with patch('vox.daemon.create_overlay', return_value=rig.overlay):
        async with running(openai_config(attenuation_enabled=False)) as h:
            h.capture.side_effect = RuntimeError('executor unavailable')
            with pytest.raises(RuntimeError, match='executor unavailable'):
                await h.daemon._start_recording()
            assert not rig.overlay._captures


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['batch', 'streaming', 'whisper_cpp'])
async def test_recording_processing_captured_mode_and_completion(rig, mode):
    config = openai_config(mode=mode, attenuation_enabled=False)
    with patch('vox.daemon.create_overlay', return_value=rig.overlay), \
         patch('vox.daemon._POST_ROLL_SECONDS', 0.01):
        async with running(config) as h:
            h.recorder.latest_level = None
            h.daemon._ready_to_record = AsyncMock(return_value=True)
            if mode == 'whisper_cpp':
                h.daemon.batch_transcriber = h.batch
            h.recorder.start.side_effect = lambda **_: h.daemon.overlay.snapshot.phase == 'hidden' or pytest.fail('shown before mic')
            gate = asyncio.Event()
            async def text(*_):
                await gate.wait()
                return 'hello world'
            h.batch.transcribe.side_effect = text
            h.streaming.finish.side_effect = text
            h.send('toggle')
            await until(lambda: rig.overlay.snapshot.phase == 'listening')
            rig.flush()
            assert rig.made[0].snapshot.status == 'Listening'
            config.mode = 'batch'  # this must not change the captured local label
            h.send('toggle')
            await until(lambda: rig.overlay.snapshot.phase == 'processing')
            rig.flush()
            assert rig.made[0].snapshot.status == ('Transcribing locally…' if mode == 'whisper_cpp' else 'Transcribing…')
            h.recorder.set_level_generation.assert_called_with(None)
            def paste_after_hide(*_):
                assert not rig.made[0].visible
                assert rig.made[0].callback is None
            h.paste.side_effect = paste_after_hide
            gate.set()
            await until(lambda: rig.overlay.snapshot.phase == 'hidden')
            assert not h.paste.called  # wait for the native hide, not merely its dispatch
            rig.flush()
            await until(lambda: h.state is State.IDLE)
            assert rig.overlay.snapshot.phase == 'hidden'
            h.paste.assert_called_once()
            rig.flush()
            assert not rig.made[0].visible


@pytest.mark.anyio
@pytest.mark.parametrize('case', ['start', 'stop', 'silence', 'empty', 'no_speech', 'error'])
async def test_unsuccessful_recordings_clear_feedback(rig, case):
    with patch('vox.daemon.create_overlay', return_value=rig.overlay), patch('vox.daemon._POST_ROLL_SECONDS', 0):
        async with running(openai_config(attenuation_enabled=False)) as h:
            h.recorder.latest_level = None
            if case == 'start':
                h.recorder.start.side_effect = AudioError('failed')
            elif case == 'stop':
                h.recorder.stop.side_effect = AudioError('failed')
            elif case == 'silence':
                h.recorder.stop.return_value = SILENT
            elif case == 'empty':
                h.batch.transcribe.return_value = ''
            elif case == 'no_speech':
                h.has_speech.return_value = False
            elif case == 'error':
                h.batch.transcribe.side_effect = RuntimeError('failed')
            await h.daemon._start_recording()
            if case != 'start':
                await h.daemon._stop_recording()
                await until(lambda: h.state is State.IDLE)
            assert rig.overlay.snapshot.phase == ('fading' if case == 'no_speech' else 'hidden')
            h.paste.assert_not_called()
            if case != 'no_speech':
                assert h.played('error')


@pytest.mark.anyio
async def test_cancel_dismisses_before_waiting_for_cleanup_and_settings_cancel(rig):
    with patch('vox.daemon.create_overlay', return_value=rig.overlay):
        async with running(openai_config(attenuation_enabled=False)) as h:
            h.recorder.latest_level = None
            await h.daemon._start_recording()
            rig.flush()
            entered, release = asyncio.Event(), asyncio.Event()
            async def restore():
                assert rig.overlay.snapshot.phase == 'hidden'
                assert rig.overlay.recorder is None
                entered.set()
                await release.wait()
            h.daemon._restore_volume = restore
            task = asyncio.create_task(h.daemon._settings_opened())
            await entered.wait()
            rig.flush()
            assert not rig.made[0].visible
            release.set()
            await task
            assert h.state is State.IDLE


@pytest.mark.anyio
async def test_config_deletion_enables_next_recording_and_settings_close_disables(rig, tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('[overlay]\nenabled = false\n')
    config = load_config(path)
    config.openai_api_key = 'test'
    rig.overlay.set_enabled(False)
    with patch('vox.daemon.create_overlay', return_value=rig.overlay), patch('vox.daemon._CONFIG_POLL_SECONDS', 0.005):
        async with running(config) as h:
            h.recorder.latest_level = None
            await h.daemon._start_recording()
            rig.flush()
            assert not rig.made
            poll = asyncio.create_task(_config_reloader(config, h.recorder, apply_config=h.daemon._apply_config))
            await asyncio.sleep(0.01)
            path.unlink()
            await until(lambda: config.overlay_enabled)
            rig.flush()
            assert not rig.made and h.state is State.RECORDING
            poll.cancel()
            await asyncio.gather(poll, return_exceptions=True)
            await h.daemon._cancel()
            await h.daemon._start_recording()
            rig.flush()
            assert rig.made[0].visible
            h.daemon._reload_api_key = AsyncMock()
            path.write_text('[overlay]\nenabled = false\n')
            await h.daemon._settings_closed()
            assert not config.overlay_enabled
            rig.flush()
            assert rig.made[0].closed and h.state is State.RECORDING


@pytest.mark.anyio
async def test_streaming_fallback_keeps_processing_and_uses_capture_guard(rig):
    with patch('vox.daemon.create_overlay', return_value=rig.overlay), patch('vox.daemon._POST_ROLL_SECONDS', 0):
        async with running(openai_config(mode='streaming', attenuation_enabled=False)) as h:
            h.recorder.latest_level = None
            h.streaming.finish.return_value = None
            gate = asyncio.Event()
            async def batch(*_):
                assert rig.overlay.snapshot.phase == 'processing'
                await gate.wait()
                return 'fallback text'
            h.batch.transcribe.side_effect = batch
            await h.daemon._start_recording()
            h.capture.assert_not_called()
            await h.daemon._stop_recording()
            await until(lambda: h.batch.transcribe.called)
            h.capture.assert_called_once_with(h.context, capture_guard=rig.overlay.capture_guard)
            gate.set()
            await until(lambda: h.state is State.IDLE)
            assert rig.overlay.snapshot.phase == 'hidden'
            h.paste.assert_called_once()


@pytest.mark.anyio
async def test_ui_failure_does_not_interrupt_audio_provider_or_paste(rig):
    rig.overlay.factory = Mock(side_effect=RuntimeError('AppKit failed'))
    with patch('vox.daemon.create_overlay', return_value=rig.overlay), patch('vox.daemon._POST_ROLL_SECONDS', 0):
        async with running(openai_config(attenuation_enabled=False)) as h:
            h.recorder.latest_level = None
            await h.daemon._start_recording()
            rig.flush()
            assert rig.overlay.failed and h.state is State.RECORDING
            await h.daemon._stop_recording()
            await until(lambda: h.state is State.IDLE)
            h.recorder.stop.assert_called_once()
            h.batch.transcribe.assert_awaited_once()
            h.paste.assert_called_once()


@pytest.mark.anyio
async def test_history_processing_state_cannot_show_overlay(rig):
    with patch('vox.daemon.create_overlay', return_value=rig.overlay):
        async with running(Config()) as h:
            h.daemon.set_state(State.PROCESSING)
            rig.flush()
            assert not rig.made and rig.overlay.snapshot.phase == 'hidden'


@pytest.mark.anyio
@pytest.mark.parametrize('processing', [False, True])
async def test_overlay_button_cancels_operation_without_paste(rig, processing):
    with patch('vox.daemon.create_overlay', return_value=rig.overlay), patch('vox.daemon._POST_ROLL_SECONDS', 0):
        async with running(openai_config(attenuation_enabled=False)) as h:
            h.recorder.latest_level = None
            gate = asyncio.Event()
            async def transcribe(*_):
                await gate.wait()
                return 'should not paste'
            h.batch.transcribe.side_effect = transcribe
            await h.daemon._start_recording()
            if processing:
                await h.daemon._stop_recording()
            rig.flush()
            rig.made[0].on_cancel(h.daemon._generation)
            assert rig.overlay.snapshot.phase == 'hidden'
            rig.flush()
            assert not rig.made[0].visible
            await until(lambda: h.state is State.IDLE)
            assert h.played('cancel')
            h.paste.assert_not_called()
            await h.daemon._start_recording()
            await h.daemon._handle(f'overlay:cancel:{h.daemon._generation - 1}')
            assert h.state is State.RECORDING
            await h.daemon._cancel()
            h.daemon._cancel = AsyncMock()
            await h.daemon._handle(f'overlay:cancel:{h.daemon._generation}')
            h.daemon._cancel.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize('how', ['overlay', 'hotkey', 'streaming_fallback', 'during_stop'])
async def test_overlay_cancel_keeps_the_interrupted_recording_for_retry(rig, tmp_path, how):
    history = HistoryDB(tmp_path / 'history.db')
    tray = Mock()
    mode = 'streaming' if how == 'streaming_fallback' else 'batch'
    post_roll = 0.2 if how == 'during_stop' else 0
    with patch('vox.daemon.create_overlay', return_value=rig.overlay), patch('vox.daemon._POST_ROLL_SECONDS', post_roll):
        async with running(openai_config(attenuation_enabled=False, mode=mode), tray, history=history) as h:
            h.recorder.latest_level = None
            h.streaming.finish.return_value = None  # the live session failed: batch takes the recording
            async def stuck(*_):
                await asyncio.Event().wait()
            h.batch.transcribe.side_effect = stuck
            if how == 'during_stop':  # the hotkey's events, so the click's event waits behind the stop
                h.send('toggle')
                await until(lambda: h.state is State.RECORDING)
                rig.flush()
                h.send('toggle')
                await until(lambda: h.state is State.PROCESSING)
                rig.flush()
                assert h.daemon.process_task is None  # × before the transcription task exists
                rig.made[0].on_cancel(h.daemon._generation)
            else:
                await h.daemon._start_recording()
                await h.daemon._stop_recording()
                await until(lambda: h.batch.transcribe.called)
                rig.flush()
            if how == 'during_stop':
                pass
            elif how == 'hotkey':
                h.send('cancel')  # the double-tap discards, as before
            else:
                rig.made[0].on_cancel(h.daemon._generation)
                h.daemon._overlay_cancel(h.daemon._generation)  # a second request still keeps it only once
            await until(lambda: h.state is State.IDLE)
            h.paste.assert_not_called()
            if how == 'hotkey':
                await asyncio.sleep(0.05)
                assert history.recent() == []
                return
            await until(lambda: history.recent())
            [rec] = history.recent()
            assert (rec.status, rec.text, rec.error_summary) == ('failed', '', CANCELLED_SUMMARY)
            assert (rec.original_mode, rec.attempted_mode) == (mode, 'batch') and rec.app_type == 'EDITOR'
            assert RecoveryStore(history).read(rec.audio_id) == SPEECH
            await until(lambda: call(CANCELLED_NOTICE) in tray.set_notice.call_args_list)
            [_, entry] = build_rows([rec], date.today())
            assert entry.preview == 'Cancelled recording'
    history.close()


@pytest.mark.anyio
@pytest.mark.parametrize('when', ['while_staging', 'before_the_event', 'staging_hangs', 'as_the_task_ends'])
async def test_quitting_right_after_overlay_cancel_still_keeps_the_recording(rig, tmp_path, when):
    history = HistoryDB(tmp_path / 'history.db')
    staging, release = threading.Event(), threading.Event()
    real_stage = RecoveryStore.stage

    def slow_stage(self, *args):
        staging.set()
        assert release.wait(5)
        return real_stage(self, *args)

    with patch('vox.daemon.create_overlay', return_value=rig.overlay), patch('vox.daemon._POST_ROLL_SECONDS', 0), \
         patch('vox.daemon._KEEP_ON_QUIT_SECONDS', 0.2 if when == 'staging_hangs' else 5), \
         patch.object(RecoveryStore, 'stage', slow_stage if when in ('while_staging', 'staging_hangs') else real_stage):
        async with running(openai_config(attenuation_enabled=False), history=history) as h:
            h.recorder.latest_level = None
            async def stuck(*_):
                await asyncio.Event().wait()
            h.batch.transcribe.side_effect = stuck
            await h.daemon._start_recording()
            await h.daemon._stop_recording()
            await until(lambda: h.batch.transcribe.called)
            rig.flush()
            if when != 'as_the_task_ends':
                rig.made[0].on_cancel(h.daemon._generation)
            if when == 'as_the_task_ends':
                h.daemon.retry = None  # no awaits in shutdown before the processing task is checked
                h.daemon._overlay_cancel(h.daemon._generation)
                h.task.cancel()  # the quit lands just as the cancelled task ends, before its callbacks run
                await h.stop()
            elif when == 'before_the_event':
                await h.stop()  # the quit overtakes the queued overlay:cancel event
                assert not h.played('cancel')
            else:
                await until(staging.is_set)
                stopping = asyncio.create_task(h.stop())  # quit while the recording is still being staged
                await asyncio.sleep(0.05)
                assert not stopping.done()
                if when == 'while_staging':
                    release.set()
                await stopping  # a staging that hangs gives up after the bound
                release.set()
    reopened = HistoryDB(tmp_path / 'history.db')
    if when == 'staging_hangs':
        assert reopened.recent() == []
    else:
        [rec] = reopened.recent()
        assert rec.error_summary == CANCELLED_SUMMARY and rec.audio_id
    reopened.close()


@pytest.mark.anyio
async def test_double_tap_preference_reloads_without_other_hotkey_changes():
    async with running(openai_config(attenuation_enabled=False)) as h:
        with patch('vox.daemon.HotkeyListener') as listener:
            new = openai_config(attenuation_enabled=False, double_tap_cancel=True)
            h.daemon._apply_hotkey(new)
            assert h.daemon.config.double_tap_cancel
            listener.assert_called_once()
            h.daemon._apply_hotkey(new)
            listener.assert_called_once()
            new.double_tap_cancel = False
            h.daemon._apply_hotkey(new)
            assert listener.call_count == 2
            assert not h.daemon.config.double_tap_cancel


@pytest.mark.anyio
@pytest.mark.parametrize('result', ['text', 'partial', 'error'])
async def test_cancel_prevents_paste_while_daemon_is_busy_reloading_settings(rig, result):
    with patch('vox.daemon.create_overlay', return_value=rig.overlay), patch('vox.daemon._POST_ROLL_SECONDS', 0):
        async with running(openai_config(attenuation_enabled=False)) as h:
            h.recorder.latest_level = None
            result_ready, reload_entered, release_reload = asyncio.Event(), asyncio.Event(), asyncio.Event()
            async def transcribe(*_):
                await result_ready.wait()
                if result == 'partial':
                    from vox.transcribe import PartialTranscriptionError
                    raise PartialTranscriptionError('provider failed', 'partial text')
                if result == 'error':
                    raise RuntimeError('provider failed')
                return 'cancelled text must never paste'
            async def reload_key(*_):
                reload_entered.set()
                await release_reload.wait()
            h.batch.transcribe.side_effect = transcribe
            h.daemon._reload_api_key = reload_key
            await h.daemon._start_recording()
            await h.daemon._stop_recording()
            rig.flush()
            process = h.daemon.process_task
            h.send('settings:closed')
            await reload_entered.wait()
            try:
                rig.made[0].on_cancel(h.daemon._generation)
                rig.flush()
                result_ready.set()
                # Let delivery's hide acknowledgment drain without unblocking daemon events.
                await until(lambda: rig.overlay._pending or process.done())
                rig.flush()
                await until(process.done)
                assert process.cancelled()
                h.paste.assert_not_called()
            finally:
                release_reload.set()
            await until(lambda: h.state is State.IDLE)
