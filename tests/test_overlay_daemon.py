"""Use the actual daemon lifecycle with controlled audio and provider boundaries."""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from test_overlay import rig as rig
from test_pipeline_daemon import SILENT, openai_config, running, until

from vox.config import Config, load_config
from vox.daemon import State, _config_reloader
from vox.errors import AudioError


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
            assert rig.overlay.snapshot.phase == ('fading' if case in ('empty', 'no_speech') else 'hidden')
            h.paste.assert_not_called()
            if case not in ('empty', 'no_speech'):
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
async def test_config_poll_deletion_and_settings_close_live_disable(rig, tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('[overlay]\nenabled = true\n')
    config = load_config(path)
    config.openai_api_key = 'test'
    with patch('vox.daemon.create_overlay', return_value=rig.overlay), patch('vox.daemon._CONFIG_POLL_SECONDS', 0.005):
        async with running(config) as h:
            h.recorder.latest_level = None
            await h.daemon._start_recording()
            rig.flush()
            poll = asyncio.create_task(_config_reloader(config, h.recorder, apply_config=h.daemon._apply_config))
            await asyncio.sleep(0.01)
            path.unlink()
            await until(lambda: not config.overlay_enabled)
            rig.flush()
            assert rig.made[0].closed and h.state is State.RECORDING
            poll.cancel()
            await asyncio.gather(poll, return_exceptions=True)
            path.write_text('[overlay]\nenabled = true\n')
            h.daemon._reload_api_key = AsyncMock()
            await h.daemon._settings_closed()
            assert config.overlay_enabled and rig.overlay.snapshot.phase == 'hidden'
            path.write_text('[overlay]\nenabled = false\n')
            await h.daemon._settings_closed()
            assert not config.overlay_enabled


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
