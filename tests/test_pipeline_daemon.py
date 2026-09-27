"""The daemon's state machine end to end, with every device, OS and network boundary replaced."""

import asyncio
import contextlib
import io
import json
import logging
import os
import threading
import tomllib
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import websockets

from vox import daemon as daemon_module
from vox.config import Config, fallback_config, load_config
from vox.daemon import (
    ACCESSIBILITY_NOTICE,
    PARTIAL_NOTICE,
    SILENT_MIC_NOTICE,
    WAYLAND_NOTICE,
    State,
    _config_reloader,
    _Daemon,
    _platform_notice,
    _process,
)
from vox.errors import AudioError, ConfigError, StreamingError, TranscriptionError
from vox.history import HistoryDB
from vox.streaming import StreamingTranscriber
from vox.transcribe import PartialTranscriptionError
from vox.window import AppContext, AppType

KEY = "sk-test-dummy-0001"


def make_wav(samples=None, rate=16000):
    if samples is None:
        samples = (np.sin(np.linspace(0, 400, rate // 2)) * 8000).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return buf.getvalue()


SPEECH = make_wav()
SILENT = make_wav(np.zeros(8000))


async def until(condition, timeout=2.0):
    """Wait for a condition instead of guessing a sleep."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


async def settle():
    """Let the daemon drain its queue, for asserting that something did NOT happen."""
    for _ in range(5):
        await asyncio.sleep(0.01)


async def idle_reloader(*args, **kwargs):
    await asyncio.sleep(3600)


def fake_streaming():
    streaming = MagicMock()
    streaming.closed = False
    streaming.connect = AsyncMock()
    streaming.send_audio_chunk = AsyncMock()
    streaming.finish = AsyncMock(return_value="streamed text")
    streaming.close = AsyncMock()
    return streaming


class Harness:
    def __init__(self, daemon, task, mocks):
        self.daemon = daemon
        self.task = task
        self.__dict__.update(mocks)

    @property
    def state(self):
        return self.daemon.state

    def send(self, *events):
        for event in events:
            self.daemon.queue.put_nowait(event)

    def played(self, name):
        return any(c.args[:1] == (name,) for c in self.sounds.play.call_args_list)

    async def stop(self):
        if not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task


@contextlib.asynccontextmanager
async def running(config, tray=None, *, history=None, cpp=None, streaming=None):
    """Run the daemon loop with fakes for the mic, sounds, volume, windows, paste and transcribers."""
    recorder = MagicMock()
    recorder.stop.return_value = SPEECH
    recorder.limit_reached = False
    recorder.refresh_input_devices.return_value = [(0, "Built-in Mic")]
    sounds = MagicMock()
    batch = MagicMock()
    batch.transcribe = AsyncMock(return_value="hello world")
    streaming = streaming or fake_streaming()
    context = AppContext("code", "main.py", AppType.EDITOR)

    with contextlib.ExitStack() as stack:
        def enter(target, **kwargs):
            return stack.enter_context(patch(target, **kwargs))

        mocks = {
            "recorder": recorder, "sounds": sounds, "batch": batch, "streaming": streaming, "context": context,
            "hotkey": enter("vox.daemon.HotkeyListener"),
            "whisper": enter("vox.daemon.Transcriber", return_value=batch),
            "cpp": enter("vox.daemon.WhisperCppTranscriber", **(cpp or {})),
            "streaming_cls": enter("vox.daemon.StreamingTranscriber", return_value=streaming),
            "detect": enter("vox.daemon.detect_active_window", return_value=context),
            "capture": enter("vox.daemon.start_screen_capture", return_value=None),
            "get_volume": enter("vox.daemon.get_volume", return_value=0.8),
            "set_volume": enter("vox.daemon.set_volume"),
            "paste": enter("vox.daemon.paste"),
            "has_speech": enter("vox.daemon.has_speech", return_value=True),
        }
        enter("vox.daemon.Recorder", return_value=recorder)
        enter("vox.daemon.SoundPlayer", return_value=sounds)
        enter("vox.daemon._open_history", return_value=history)
        enter("vox.daemon._config_reloader", side_effect=idle_reloader)
        enter("vox.daemon._platform_notice", return_value=None)

        daemon = _Daemon(config, tray)
        harness = Harness(daemon, asyncio.create_task(daemon.run()), mocks)
        try:
            yield harness
        finally:
            await harness.stop()


def openai_config(**kwargs):
    return Config(openai_api_key=KEY, **kwargs)


# -- Event-loop resilience ---------------------------------------------------------


@pytest.mark.anyio
async def test_an_unexpected_error_is_logged_and_the_daemon_keeps_running(caplog):
    async with running(openai_config(attenuation_level=0.5)) as h:
        h.detect.side_effect = RuntimeError("window lookup exploded")
        h.send("toggle")
        await until(lambda: h.played("error"))

        assert h.state is State.IDLE
        h.set_volume.assert_called_with(0.8)  # attenuated to 0.4, then restored
        h.recorder.discard.assert_called()
        [record] = [r for r in caplog.records if "Unexpected error handling 'toggle'" in r.getMessage()]
        assert record.exc_info  # a traceback, not just the message

        h.detect.side_effect = None
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        assert not h.task.done()


@pytest.mark.anyio
async def test_quitting_mid_recording_restores_the_volume_and_releases_everything():
    async with running(openai_config(attenuation_level=0.5)) as h:
        h.send("toggle")
        await until(lambda: h.set_volume.call_args is not None)
        h.set_volume.assert_called_with(0.4)

        await h.stop()

        h.set_volume.assert_called_with(0.8)
        h.recorder.close.assert_called()
        h.hotkey.return_value.stop.assert_called()


@pytest.mark.anyio
async def test_quitting_while_the_volume_is_being_lowered_still_restores_it():
    in_call, release = threading.Event(), threading.Event()

    def slow_get_volume():  # e.g. osascript taking its time
        in_call.set()
        release.wait(2)
        return 0.8

    async with running(openai_config(attenuation_level=0.5)) as h:
        h.get_volume.side_effect = slow_get_volume
        h.send("toggle")
        await until(in_call.is_set)
        threading.Timer(0.05, release.set).start()
        await h.stop()

    assert [c.args for c in h.set_volume.call_args_list] == [(0.4,), (0.8,)]


@pytest.mark.anyio
async def test_a_double_tap_before_processing_starts_closes_the_live_session():
    async with running(openai_config(mode="streaming")) as h:
        h.send("toggle")
        await until(lambda: h.streaming.connect.await_count == 1)

        h.send("toggle", "cancel")  # the second tap lands while the first is still stopping
        await until(lambda: h.streaming.close.await_count >= 1)

        assert h.state is State.IDLE
        assert h.played("cancel")
        h.streaming.finish.assert_not_called()
        h.paste.assert_not_called()


@pytest.mark.anyio
async def test_a_microphone_that_will_not_open_leaves_vox_idle():
    async with running(openai_config()) as h:
        h.recorder.start.side_effect = AudioError("Failed to start audio stream: no device")
        h.send("toggle")
        await until(lambda: h.played("error"))
        assert h.state is State.IDLE
        assert h.daemon.session is None

        h.recorder.start.side_effect = None
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)


@pytest.mark.anyio
async def test_a_failed_stop_abandons_the_recording_and_its_screen_capture():
    screen = asyncio.get_running_loop().create_future()
    async with running(openai_config()) as h:
        h.capture.return_value = screen
        h.recorder.stop.side_effect = AudioError("No audio data recorded")
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("toggle")
        await until(lambda: h.played("error"))

        assert h.state is State.IDLE
        assert screen.cancelled()
        h.batch.transcribe.assert_not_called()


@pytest.mark.anyio
async def test_toggle_while_processing_is_busy_and_a_stray_done_event_is_ignored():
    release = asyncio.Event()

    async def slow_transcribe(wav, context):
        await release.wait()
        return "finally"

    async with running(openai_config()) as h:
        h.batch.transcribe.side_effect = slow_transcribe
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("toggle")
        await until(lambda: h.batch.transcribe.await_count == 1)

        h.send("toggle")
        await until(lambda: h.played("busy"))
        h.send("process_done")  # e.g. late from a cancelled transcription
        await settle()
        assert h.state is State.PROCESSING

        release.set()
        await until(lambda: h.state is State.IDLE)
        h.paste.assert_called_once_with("finally", AppType.EDITOR)


@pytest.mark.anyio
async def test_pause_discards_a_recording_and_blocks_new_ones_until_resumed():
    tray = MagicMock()
    async with running(openai_config(), tray) as h:
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("pause")
        await until(lambda: h.state is State.IDLE)
        h.recorder.discard.assert_called()
        tray.set_paused.assert_called_with(True)

        h.send("toggle")
        await until(lambda: h.played("busy"))
        assert h.state is State.IDLE

        h.send("resume", "toggle")
        await until(lambda: h.state is State.RECORDING)


# -- Transcription mode ---------------------------------------------------------------


@pytest.mark.anyio
async def test_a_failed_mode_switch_changes_nothing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[transcription]\nmode = "batch"\n')
    config = openai_config()
    config._config_path = path
    tray = MagicMock()

    async with running(config, tray, cpp={"side_effect": ConfigError("whisper.cpp model not found: x.bin")}) as h:
        h.send("mode:whisper_cpp")
        await until(lambda: h.played("error"))
        assert config.mode == "batch"
        assert path.read_text() == '[transcription]\nmode = "batch"\n'
        tray.mode_changed.assert_not_called()

        h.sounds.reset_mock()
        h.send("mode:bogus")
        await until(lambda: h.played("error"))
        assert config.mode == "batch"

        h.send("mode:streaming")
        await until(lambda: tray.mode_changed.called)
        assert config.mode == "streaming"
        assert tomllib.loads(path.read_text())["transcription"]["mode"] == "streaming"


@pytest.mark.anyio
async def test_switching_to_local_transcription_tries_its_setup_once(tmp_path):
    local = MagicMock()
    config = openai_config()
    config._config_path = tmp_path / "config.toml"
    tray = MagicMock()
    async with running(config, tray, cpp={"return_value": local}) as h:
        h.send("mode:whisper_cpp")
        await until(lambda: tray.mode_changed.called)
        assert config.mode == "whisper_cpp"
        assert h.daemon.batch_transcriber is local
        h.cpp.assert_called_once_with(config)  # building it is the setup check
    assert tomllib.loads(config.config_path.read_text())["transcription"]["mode"] == "whisper_cpp"


@pytest.mark.anyio
async def test_an_openai_mode_needs_a_key_before_it_can_be_chosen(tmp_path):
    config = Config(mode="whisper_cpp")
    config._config_path = tmp_path / "config.toml"
    tray = MagicMock()
    async with running(config, tray, cpp={"return_value": MagicMock()}) as h:
        h.send("mode:streaming")
        await until(lambda: h.played("error"))
        assert config.mode == "whisper_cpp"
        assert not config.config_path.exists()
        tray.mode_changed.assert_not_called()


@pytest.mark.anyio
async def test_a_mode_that_cannot_run_blocks_recording_and_is_retried_on_each_toggle():
    local = MagicMock()
    cpp = {"side_effect": [ConfigError("model not found"), ConfigError("model not found"), local]}
    config = Config(mode="whisper_cpp")
    tray = MagicMock()

    async with running(config, tray, cpp=cpp) as h:
        assert config.mode_error == "model not found"

        h.send("toggle")
        await until(lambda: h.played("error"))
        assert h.state is State.IDLE
        h.recorder.start.assert_not_called()
        tray.mode_changed.assert_not_called()  # still the same error

        h.send("toggle")  # the user fixed the model path meanwhile
        await until(lambda: h.state is State.RECORDING)
        assert config.mode_error is None
        assert h.daemon.batch_transcriber is local
        tray.mode_changed.assert_called()


@pytest.mark.anyio
async def test_a_mode_edited_in_the_file_gets_its_own_transcriber_at_the_next_toggle():
    local = MagicMock()
    config = openai_config()
    async with running(config, cpp={"return_value": local}) as h:
        assert h.daemon.batch_transcriber is h.batch
        config.mode = "whisper_cpp"  # what the config reloader does
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        assert h.daemon.batch_transcriber is local


# -- Recording limit -----------------------------------------------------------------


@pytest.mark.anyio
async def test_the_recording_limit_stops_and_transcribes(caplog):
    caplog.set_level(logging.INFO, logger="vox.daemon")
    async with running(openai_config(max_recording_seconds=300)) as h:
        h.send("limit")  # nothing to stop
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        on_limit = h.recorder.start.call_args.kwargs["on_limit"]

        h.send("limit")  # stale: from an earlier recording, this one has not reached it
        await settle()
        assert h.state is State.RECORDING

        h.recorder.limit_reached = True
        on_limit()  # what the recorder calls on the event loop
        await until(lambda: h.paste.called)
        await until(lambda: h.state is State.IDLE)
        assert "Recording limit reached (300 s)" in caplog.text
        h.batch.transcribe.assert_awaited_once()


@pytest.mark.anyio
async def test_a_limit_picked_from_the_menu_is_saved_and_applies_next_time(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("# my settings\n[audio]\nsample_rate = 48000\n")
    config = openai_config()
    config._config_path = path
    tray = MagicMock()

    async with running(config, tray) as h:
        h.send("limit:600")
        await until(lambda: tray.limit_changed.called)
        assert config.max_recording_seconds == 600
        assert tomllib.loads(path.read_text())["audio"] == {"sample_rate": 48000, "max_recording_seconds": 600}
        assert path.read_text().startswith("# my settings")

        for bad in ("limit:abc", "limit:0", "limit:-5"):
            h.sounds.reset_mock()
            h.send(bad)
            await until(lambda: h.played("error"))
        assert config.max_recording_seconds == 600
        assert tray.limit_changed.call_count == 1


# -- A settings file that does not load -------------------------------------------------


def broken_settings(tmp_path, text='[transcription]\nmode = "whisper_cpp"\n[attenuation]\nlevel = "loud"\n'):
    """What __main__ starts on when config.toml does not load: defaults, and why."""
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(ConfigError) as error:
        load_config(path)
    return fallback_config(path, error.value)


@pytest.mark.anyio
async def test_a_settings_file_that_does_not_load_blocks_recording_until_it_does(tmp_path, caplog):
    """The file may choose local-only transcription, so audio must not go to OpenAI on the defaults."""
    config = broken_settings(tmp_path)
    tray = MagicMock()
    async with running(config, tray) as h:
        h.send("toggle")
        await until(lambda: h.played("error"))
        assert h.state is State.IDLE
        h.recorder.start.assert_not_called()
        assert '[attenuation] level must be a number from 0 to 1, not "loud"' in caplog.text
        tray.open_key_window.assert_not_called()  # there is no key, but the file may not need one

        config.config_error = None  # what the reloader does once the file loads
        config.openai_api_key = KEY
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)


@pytest.mark.anyio
async def test_menu_changes_are_refused_while_the_settings_file_does_not_load(tmp_path):
    config = broken_settings(tmp_path)
    config.openai_api_key = KEY
    before = config.config_path.read_text()
    tray = MagicMock()
    async with running(config, tray) as h:
        h.send("mode:streaming", "limit:600")
        await until(lambda: h.sounds.play.call_count == 2)
        assert [c.args for c in h.sounds.play.call_args_list] == [("error",), ("error",)]
    assert config.config_path.read_text() == before
    assert (config.mode, config.max_recording_seconds) == ("batch", 900)
    tray.mode_changed.assert_not_called()
    tray.limit_changed.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("broken", ["[audio\nsample_rate = 48000\n", "audio = 5\ntranscription = 1\n"])
async def test_menu_changes_to_a_file_broken_since_it_loaded_fail_with_the_error_sound(tmp_path, caplog, broken):
    path = tmp_path / "config.toml"
    path.write_text("[audio]\nsample_rate = 48000\n")
    config = load_config(path)
    config.openai_api_key = KEY
    tray = MagicMock()
    async with running(config, tray) as h:
        path.write_text(broken)  # an edit the reloader rejected, so config_error stays unset
        h.send("limit:600")
        await until(lambda: h.played("error"))
        h.sounds.reset_mock()
        h.send("mode:streaming")
        await until(lambda: h.played("error"))
        assert h.state is State.IDLE and not h.task.done()
    assert path.read_text() == broken
    assert (config.mode, config.max_recording_seconds) == ("batch", 900)
    tray.mode_changed.assert_not_called()
    tray.limit_changed.assert_not_called()
    assert "Unexpected error" not in caplog.text


# -- Tray notices and devices ----------------------------------------------------------


@pytest.mark.anyio
async def test_the_device_list_goes_to_the_tray_at_startup_and_only_when_it_changes():
    tray = MagicMock()
    with patch("vox.daemon._DEVICE_REFRESH_DELAY", 0.01):
        async with running(openai_config(), tray) as h:
            await until(lambda: tray.devices_changed.called)
            tray.devices_changed.assert_called_once_with([(0, "Built-in Mic")])

            h.send("toggle", "cancel")  # back to idle: re-scan, same devices
            await until(lambda: h.recorder.refresh_input_devices.call_count >= 2)
            await settle()
            assert tray.devices_changed.call_count == 1

            h.recorder.refresh_input_devices.return_value = [(0, "Built-in Mic"), (3, "USB Mic")]
            h.send("toggle", "cancel")
            await until(lambda: tray.devices_changed.call_count == 2)
            tray.devices_changed.assert_called_with([(0, "Built-in Mic"), (3, "USB Mic")])

            h.recorder.refresh_input_devices.return_value = None  # a stream was open: no answer
            h.send("toggle", "cancel")
            await until(lambda: h.recorder.refresh_input_devices.call_count >= 4)
            await settle()
            assert tray.devices_changed.call_count == 2


@pytest.mark.anyio
async def test_a_new_event_cancels_a_pending_device_rescan():
    tray = MagicMock()
    async with running(openai_config(), tray) as h:
        await until(lambda: h.recorder.refresh_input_devices.called)
        with patch("vox.daemon._DEVICE_REFRESH_DELAY", 60):
            h.send("toggle", "cancel", "toggle")
            await until(lambda: h.state is State.RECORDING and h.recorder.start.call_count == 2)
        assert h.daemon._device_timer is None
        assert h.recorder.refresh_input_devices.call_count == 1


@pytest.mark.anyio
async def test_devices_are_rescanned_every_so_often_while_idle_where_that_is_cheap():
    tray = MagicMock()
    with patch("vox.daemon._IDLE_DEVICE_SCAN_SECONDS", 0.01), patch("vox.daemon._DEVICE_REFRESH_DELAY", 0.01):
        async with running(openai_config(), tray) as h:
            scans = h.recorder.refresh_input_devices
            await until(lambda: scans.call_count >= 3)

            h.send("toggle")
            await until(lambda: h.state is State.RECORDING)
            count = scans.call_count
            await settle()
            assert scans.call_count == count  # never while recording

            h.send("cancel")
            await until(lambda: scans.call_count >= count + 2)


@pytest.mark.anyio
async def test_without_idle_rescans_devices_are_rescanned_only_on_return_to_idle():
    tray = MagicMock()
    with patch("vox.daemon._IDLE_DEVICE_SCAN_SECONDS", None), patch("vox.daemon._DEVICE_REFRESH_DELAY", 0.01):
        async with running(openai_config(), tray) as h:
            scans = h.recorder.refresh_input_devices
            await until(lambda: scans.called)
            await settle()
            assert scans.call_count == 1

            h.send("toggle", "cancel")
            await until(lambda: scans.call_count == 2)
            await settle()
            assert scans.call_count == 2


@pytest.mark.anyio
async def test_a_silent_microphone_shows_a_notice_until_audio_returns():
    tray = MagicMock()
    async with running(openai_config(), tray) as h:
        h.recorder.stop.return_value = SILENT
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("toggle")
        await until(lambda: h.played("error"))
        assert h.state is State.IDLE
        tray.set_notice.assert_called_with(SILENT_MIC_NOTICE)
        h.batch.transcribe.assert_not_called()

        h.recorder.stop.return_value = SPEECH
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("toggle")
        await until(lambda: h.paste.called)
        tray.set_notice.assert_called_with(None)  # back to the platform notice, here none
        assert tray.set_notice.call_count == 2


@pytest.mark.anyio
async def test_the_silent_microphone_check_runs_off_the_event_loop():
    threads = []

    def check(wav):  # reads every sample of what may be an hour of audio
        threads.append(threading.current_thread())
        return False

    with patch("vox.daemon.is_digital_silence", side_effect=check):
        async with running(openai_config()) as h:
            h.send("toggle")
            await until(lambda: h.state is State.RECORDING)
            h.send("toggle")
            await until(lambda: h.paste.called)
    assert threads and threads[0] is not threading.current_thread()


@pytest.mark.anyio
async def test_a_partly_transcribed_dictation_is_noticed_until_the_next_one_succeeds(tmp_path):
    history = HistoryDB(tmp_path / "history.db")
    tray = MagicMock()
    async with running(openai_config(), tray, history=history) as h:
        h.batch.transcribe.side_effect = PartialTranscriptionError("part 2 of 2 failed: 500", "the first part")
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("toggle")
        await until(lambda: tray.set_notice.called)
        tray.set_notice.assert_called_once_with(PARTIAL_NOTICE)
        assert h.played("error")
        assert history.recent(1)[0].text == "the first part"

        h.batch.transcribe.side_effect = TranscriptionError("Transcription API failed: 500")
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("toggle")
        await until(lambda: h.batch.transcribe.await_count == 2 and h.state is State.IDLE)
        assert tray.set_notice.call_count == 1  # a failed dictation is no reason to drop the notice

        h.batch.transcribe.side_effect = None
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("toggle")
        await until(lambda: tray.set_notice.call_count == 2)
        tray.set_notice.assert_called_with(None)  # back to the platform notice, here none
    history.close()


@pytest.mark.parametrize(
    ("env", "notice"),
    [
        ({"XDG_SESSION_TYPE": "wayland"}, WAYLAND_NOTICE),
        ({"WAYLAND_DISPLAY": "wayland-0"}, WAYLAND_NOTICE),
        ({"XDG_SESSION_TYPE": "x11"}, None),
        ({}, None),
    ],
)
def test_linux_platform_notice(monkeypatch, env, notice):
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with patch.object(daemon_module.sys, "platform", "linux"):
        assert _platform_notice() == notice


@pytest.mark.parametrize(("trusted", "notice"), [(True, None), (False, ACCESSIBILITY_NOTICE)])
def test_macos_platform_notice(trusted, notice):
    with patch.object(daemon_module.sys, "platform", "darwin"), \
         patch("vox.daemon.check_accessibility_permission", return_value=trusted) as check:
        assert _platform_notice() == notice
    check.assert_called_once_with(prompt=False)  # never pops the system dialog by itself


def test_run_puts_the_platform_notice_in_the_tray():
    tray = MagicMock()

    async def fake_main(config, tray=None):
        pass

    with patch("vox.ui.tray.create_tray", return_value=tray), \
         patch("vox.daemon._platform_notice", return_value=WAYLAND_NOTICE), \
         patch("vox.daemon._main", side_effect=fake_main):
        daemon_module.run(openai_config())
    tray.set_notice.assert_called_once_with(WAYLAND_NOTICE)
    tray.run.assert_called_once()


# -- Screen context -------------------------------------------------------------------


@pytest.mark.anyio
async def test_batch_recording_captures_the_screen_only_when_screen_context_is_on():
    screen = asyncio.get_running_loop().create_future()
    screen.set_result("KubeClient handleRequest")
    async with running(openai_config()) as h:
        h.capture.return_value = screen
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("toggle")
        await until(lambda: h.paste.called)
        h.capture.assert_called_once()
        assert h.batch.transcribe.call_args.args[1].screen_text == "KubeClient handleRequest"

    async with running(openai_config(context_screen=False)) as h:
        h.send("toggle")
        await until(lambda: h.state is State.RECORDING)
        h.send("toggle")
        await until(lambda: h.paste.called)
        h.capture.assert_not_called()
        assert h.batch.transcribe.call_args.args[1].screen_text == ""


@pytest.mark.anyio
async def test_a_screen_capture_that_never_finishes_does_not_hold_up_the_dictation():
    never = asyncio.get_running_loop().create_future()
    with patch("vox.daemon._SCREEN_WAIT_SECONDS", 0.01):
        async with running(openai_config()) as h:
            h.capture.return_value = never
            h.send("toggle")
            await until(lambda: h.state is State.RECORDING)
            h.send("toggle")
            await until(lambda: h.paste.called)
    assert h.batch.transcribe.call_args.args[1].screen_text == ""
    assert never.cancelled()


@pytest.mark.anyio
async def test_streaming_recording_starts_no_screen_capture():
    async with running(openai_config(mode="streaming")) as h:
        h.send("toggle")
        await until(lambda: h.streaming.connect.await_count == 1)
        h.send("toggle")
        await until(lambda: h.paste.called)
        h.capture.assert_not_called()
        h.paste.assert_called_once_with("streamed text", AppType.EDITOR)
        h.batch.transcribe.assert_not_called()


async def endless_chunks():
    await asyncio.Event().wait()  # a recording that is still running
    yield b""


@pytest.mark.anyio
async def test_a_live_session_that_fails_mid_recording_stops_the_audio_queue():
    async with running(openai_config(mode="streaming")) as h:
        h.recorder.stream_chunks = endless_chunks
        h.streaming.connect.side_effect = StreamingError("Failed to connect")
        h.send("toggle")
        await until(lambda: h.recorder.stop_streaming.called)
        assert h.state is State.RECORDING  # the recording itself goes on, for the batch fallback


@pytest.mark.anyio
async def test_a_cancelled_live_session_leaves_the_next_recordings_audio_queue_alone():
    async with running(openai_config(mode="streaming")) as h:
        h.recorder.stream_chunks = endless_chunks
        h.send("toggle")
        await until(lambda: h.streaming.connect.await_count == 1)
        h.send("cancel", "toggle")  # the old worker ends only once the new recording has started
        await until(lambda: h.streaming.connect.await_count == 2)
        await settle()
        assert h.state is State.RECORDING
        h.recorder.stop_streaming.assert_not_called()


# -- _process: paste, history, errors ----------------------------------------------------


def process_kwargs(**overrides):
    batch = MagicMock()
    batch.transcribe = AsyncMock(return_value="hello world")
    kwargs = {
        "wav_data": SPEECH, "config": Config(mode="batch"), "batch_transcriber": batch,
        "streaming_transcriber": None, "stream_task": None, "sounds": MagicMock(),
        "queue": asyncio.Queue(), "context": AppContext("code", "main.py", AppType.EDITOR),
        "screen_capture_future": None, "mode": "batch",
    }
    kwargs.update(overrides)
    return kwargs


@pytest.fixture
def speech():
    with patch("vox.daemon.has_speech", return_value=True):
        yield


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("focused", "expected"),
    [
        (AppContext("com.mitchellh.ghostty", "zsh", AppType.TERMINAL), AppType.TERMINAL),
        (AppContext("", "", AppType.OTHER), AppType.EDITOR),  # nothing detected: keep the stop-time guess
    ],
)
async def test_linux_paste_uses_the_window_focused_at_paste_time(speech, focused, expected):
    with patch.object(daemon_module.sys, "platform", "linux"), \
         patch("vox.daemon.detect_active_window", return_value=focused), \
         patch("vox.daemon.paste") as paste:
        await _process(**process_kwargs())
    paste.assert_called_once_with("hello world", expected)


@pytest.mark.anyio
async def test_macos_paste_does_not_look_the_window_up_again(speech):
    with patch.object(daemon_module.sys, "platform", "darwin"), \
         patch("vox.daemon.detect_active_window") as detect, \
         patch("vox.daemon.paste") as paste:
        await _process(**process_kwargs())
    detect.assert_not_called()
    paste.assert_called_once_with("hello world", AppType.EDITOR)


@pytest.mark.anyio
async def test_snippets_match_ignoring_case_and_punctuation_and_stay_out_of_info_logs(speech, caplog):
    kwargs = process_kwargs(config=Config(snippets={"My Email": "alex@example.com"}))
    kwargs["batch_transcriber"].transcribe.return_value = "my email!"
    caplog.set_level(logging.INFO, logger="vox")
    with patch("vox.daemon.detect_active_window", return_value=AppContext("", "", AppType.OTHER)), \
         patch("vox.daemon.paste") as paste:
        await _process(**kwargs)
    paste.assert_called_once_with("alex@example.com", AppType.EDITOR)
    assert "Expanded a snippet (16 chars)" in caplog.text
    assert "alex@example.com" not in caplog.text
    assert "my email" not in caplog.text


@pytest.mark.anyio
async def test_history_records_the_mode_the_recording_started_in(tmp_path, speech):
    history = HistoryDB(tmp_path / "history.db")
    streaming = fake_streaming()
    config = Config(mode="batch")  # the file was edited while the streaming recording was transcribed
    with patch("vox.daemon.detect_active_window", return_value=AppContext("", "", AppType.OTHER)), \
         patch("vox.daemon.paste") as paste:
        await _process(**process_kwargs(
            config=config, streaming_transcriber=streaming, history=history, mode="streaming",
        ))
    paste.assert_called_once_with("streamed text", AppType.EDITOR)
    [rec] = history.search()
    assert rec.transcription_mode == "streaming"
    history.close()


@pytest.mark.anyio
async def test_history_records_batch_when_a_streaming_recording_fell_back_to_it(tmp_path, speech):
    history = HistoryDB(tmp_path / "history.db")
    streaming = fake_streaming()
    streaming.finish.side_effect = StreamingError("Connection closed before the transcript completed")
    kwargs = process_kwargs(
        config=Config(mode="streaming", context_screen=False), streaming_transcriber=streaming,
        history=history, mode="streaming",
    )
    with patch("vox.daemon.detect_active_window", return_value=AppContext("", "", AppType.OTHER)), \
         patch("vox.daemon.paste") as paste:
        await _process(**kwargs)
    paste.assert_called_once_with("hello world", AppType.EDITOR)
    [rec] = history.search()
    assert rec.transcription_mode == "batch"
    history.close()


@pytest.mark.anyio
async def test_a_partial_transcription_is_kept_in_history(tmp_path, speech):
    history = HistoryDB(tmp_path / "history.db")
    tray = MagicMock()
    kwargs = process_kwargs(history=history, tray=tray)
    kwargs["batch_transcriber"].transcribe.side_effect = PartialTranscriptionError(
        "part 2 of 3 failed: rate limited", "the first ten minutes",
    )
    with patch("vox.daemon.paste") as paste:
        await _process(**kwargs)
    paste.assert_not_called()
    kwargs["sounds"].play.assert_called_once_with("error")
    [rec] = history.search()
    assert rec.text == "the first ten minutes"
    tray.history_changed.assert_called_once()
    assert kwargs["queue"].get_nowait() == "process_done"
    history.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "traceback"),
    [(RuntimeError("a bug"), True), (TranscriptionError("Transcription API failed: 500"), False)],
)
async def test_processing_errors_log_a_traceback_only_for_bugs(speech, caplog, error, traceback):
    kwargs = process_kwargs()
    kwargs["batch_transcriber"].transcribe.side_effect = error
    with patch("vox.daemon.paste") as paste:
        await _process(**kwargs)
    paste.assert_not_called()
    kwargs["sounds"].play.assert_called_once_with("error")
    [record] = [r for r in caplog.records if r.getMessage().startswith("Processing error")]
    assert bool(record.exc_info) == traceback
    assert kwargs["queue"].get_nowait() == "process_done"


# -- Streaming fallback against a real (local) WebSocket server ----------------------------


async def _connected(handler):
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    transcriber = StreamingTranscriber(Config(openai_api_key=KEY), ws_url=f"ws://127.0.0.1:{port}")
    await transcriber.connect()
    return server, transcriber


async def hang_up_on_commit(websocket):
    async for message in websocket:
        if json.loads(message).get("type") == "input_audio_buffer.commit":
            await websocket.close()


async def error_on_commit(websocket):
    async for message in websocket:
        if json.loads(message).get("type") == "input_audio_buffer.commit":
            await websocket.send(json.dumps({"type": "error", "error": {"message": "server overloaded"}}))


@pytest.mark.anyio
@pytest.mark.parametrize("handler", [hang_up_on_commit, error_on_commit])
async def test_a_failed_live_session_falls_back_to_batch(handler):
    server, transcriber = await _connected(handler)
    try:
        stream_task = asyncio.create_task(asyncio.sleep(0))
        kwargs = process_kwargs(
            config=Config(mode="streaming", openai_api_key=KEY, context_screen=False),
            streaming_transcriber=transcriber, stream_task=stream_task, mode="streaming",
        )
        kwargs["batch_transcriber"].transcribe.return_value = "from the batch fallback"
        with patch("vox.daemon.paste") as paste, \
             patch("vox.daemon.detect_active_window", return_value=AppContext("", "", AppType.OTHER)):
            await asyncio.wait_for(_process(**kwargs), 5)
        paste.assert_called_once_with("from the batch fallback", AppType.EDITOR)
        assert transcriber.closed
        kwargs["sounds"].play.assert_not_called()
    finally:
        server.close()
        await server.wait_closed()


# -- Config reload -----------------------------------------------------------------------


@contextlib.asynccontextmanager
async def reloading(config, recorder, tray=None):
    """Run the real config reloader, polling every 10 ms."""
    with patch("vox.daemon._CONFIG_POLL_SECONDS", 0.01):
        task = asyncio.create_task(_config_reloader(config, recorder, tray))
        await asyncio.sleep(0.02)  # it notes the file as it is first
        try:
            yield task
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.anyio
async def test_config_reload_applies_screen_context_and_the_recording_limit(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[audio]\nsample_rate = 48000\n")
    config = load_config(path)
    recorder = MagicMock()
    tray = MagicMock()

    def rewrite(text):
        mtime = path.stat().st_mtime
        path.write_text(text)
        os.utime(path, (mtime + 10, mtime + 10))

    async with reloading(config, recorder, tray) as task:
        rewrite("[audio]\nsample_rate = 48000\nmax_recording_seconds = 300\n[context]\nscreen = false\n")
        await until(lambda: not config.context_screen)
        assert config.max_recording_seconds == 300
        recorder.reconfigure.assert_not_called()  # audio settings unchanged
        await until(lambda: tray.mode_changed.called)  # the menu shows the new limit

        rewrite("[audio]\nsample_rate = 16000\n[context]\nscreen = false\n")
        await until(lambda: recorder.reconfigure.called)
        assert config.sample_rate == 16000
        assert config.max_recording_seconds == 900

        calls = tray.mode_changed.call_count
        rewrite("[transcription]\nmode = 'nonsense'\n")  # an invalid edit keeps the last good settings
        await asyncio.sleep(0.1)
        assert config.mode == "batch" and not task.done()
        assert config.config_error is None
        assert tray.mode_changed.call_count == calls


@pytest.mark.anyio
async def test_a_settings_file_that_loads_again_is_applied_and_lets_vox_record(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="vox.daemon")
    config = broken_settings(tmp_path)
    path = config.config_path
    tray = MagicMock()

    async with reloading(config, MagicMock(), tray):
        path.write_text("[transcription]\nmode = 'streaming'\n[attenuation]\nlevel = 'still wrong'\n")
        await until(lambda: "Config reload failed" in caplog.text)
        assert config.config_error is not None
        tray.mode_changed.assert_not_called()

        # A backup moved back into place keeps its older time, so the time alone doesn't show the change
        backup = tmp_path / "config.toml.bak"
        backup.write_text('[transcription]\nmode = "streaming"\n[attenuation]\nlevel = 0.3\n[hotkey]\nkey = "right_ctrl"\n')
        os.utime(backup, (1_000_000, 1_000_000))
        os.replace(backup, path)
        await until(lambda: config.config_error is None)

    assert (config.mode, config.attenuation_level) == ("streaming", 0.3)
    tray.mode_changed.assert_called_once()  # the status line drops "Settings file has an error"
    assert config.hotkey == "right_shift"  # the listener started with the default, until a restart
    assert "hotkey settings in" in caplog.text and "take effect when Vox restarts" in caplog.text


@pytest.mark.anyio
async def test_a_broken_settings_file_that_is_deleted_lets_vox_record_on_the_defaults(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="vox.daemon")
    config = broken_settings(tmp_path)
    tray = MagicMock()

    async with reloading(config, MagicMock(), tray):
        config.config_path.unlink()
        await until(lambda: config.config_error is None)

    assert config.mode == "batch"
    tray.mode_changed.assert_called_once()
    assert "is gone: Vox is using the default settings" in caplog.text


@pytest.mark.anyio
async def test_a_settings_file_fixed_before_the_reloader_starts_is_applied(tmp_path):
    config = broken_settings(tmp_path)
    config.config_path.write_text('[transcription]\nmode = "whisper_cpp"\n[attenuation]\nlevel = 0.2\n')

    async with reloading(config, MagicMock()):  # its first look at the file sees the fixed version
        await until(lambda: config.config_error is None)

    assert (config.mode, config.attenuation_level) == ("whisper_cpp", 0.2)


@pytest.mark.anyio
@pytest.mark.skipif(os.geteuid() == 0, reason="root reads the file whatever its permissions")
async def test_an_unreadable_settings_file_is_applied_once_it_is_readable(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[transcription]\nmode = "whisper_cpp"\n')
    path.chmod(0)
    with pytest.raises(ConfigError, match="Permission denied") as error:
        load_config(path)
    config = fallback_config(path, error.value)

    async with reloading(config, MagicMock()):
        path.chmod(0o600)  # changes neither the time, the size nor the inode
        await until(lambda: config.config_error is None)

    assert config.mode == "whisper_cpp"


@pytest.mark.anyio
async def test_a_later_edit_that_still_fails_replaces_the_error_and_is_logged_once(tmp_path, caplog):
    config = broken_settings(tmp_path)
    tray = MagicMock()

    async with reloading(config, MagicMock(), tray):
        await settle()
        assert "Config reload failed" not in caplog.text  # retries that fail as at startup stay quiet

        config.config_path.write_text("[audio]\nsample_rate = 0\n")
        await until(lambda: "sample_rate" in config.config_error)
        await settle()  # several more polls retry the file

    assert caplog.text.count("Config reload failed") == 1
    tray.mode_changed.assert_not_called()
