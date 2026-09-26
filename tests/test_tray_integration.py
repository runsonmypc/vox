"""Integration tests: daemon state propagation to the tray, history persistence, and headless fallback."""

import asyncio
import io
import os
import sys
import wave
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from vox import keystore
from vox.config import Config, load_config
from vox.history import HistoryDB
from vox.window import AppContext, AppType


def _wav(duration_s=0.5, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.sin(np.linspace(0, 200, int(rate * duration_s))) * 8000).astype(np.int16).tobytes())
    return buf.getvalue()


class RecordingTray:
    """Stands in for TrayManager and records what the daemon tells it."""

    def __init__(self):
        self.states = []
        self.paused = []
        self.history_changes = 0
        self.attached = None
        self.stopped = False
        self.key_windows = 0
        self.key_changes = 0

    def attach(self, loop, queue, history, main_task):
        self.attached = (loop, queue, history, main_task)

    def set_state(self, state):
        self.states.append(state)

    def set_paused(self, paused):
        self.paused.append(paused)

    def history_changed(self):
        self.history_changes += 1

    def open_key_window(self):
        self.key_windows += 1

    def key_changed(self):
        self.key_changes += 1

    def stop(self):
        self.stopped = True


@contextmanager
def daemon_env(transcript="hello world"):
    """Patch the daemon's hardware and network dependencies; yield the mocks."""
    env = {
        "recorder": MagicMock(),
        "sounds": MagicMock(),
        "paste": MagicMock(),
        "batch": MagicMock(),
    }
    env["recorder"].stop.return_value = _wav()
    env["batch"].transcribe = AsyncMock(return_value=transcript)

    async def idle_reloader(*args, **kwargs):
        await asyncio.Event().wait()

    loop = asyncio.get_running_loop()
    with patch("vox.daemon.HotkeyListener"), \
         patch("vox.daemon.Recorder", return_value=env["recorder"]), \
         patch("vox.daemon.SoundPlayer", return_value=env["sounds"]), \
         patch("vox.daemon.WhisperTranscriber", return_value=env["batch"]), \
         patch("vox.daemon._config_reloader", side_effect=idle_reloader), \
         patch("vox.daemon.start_screen_capture", side_effect=lambda ctx: loop.create_future()), \
         patch("vox.daemon.detect_active_window", return_value=AppContext("code", "VSCode", AppType.EDITOR)), \
         patch("vox.daemon.has_speech", return_value=True), \
         patch("vox.daemon.paste", env["paste"]), \
         patch("vox.daemon.get_volume", return_value=None):
        yield env


async def wait_for(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def start_daemon(tray, config=None):
    from vox.daemon import _main

    config = config or Config(mode="batch", openai_api_key="test", attenuation_enabled=False)
    task = asyncio.create_task(_main(config, tray))
    await wait_for(lambda: tray.attached is not None if isinstance(tray, RecordingTray) else tray._queue is not None)
    return task


async def stop_daemon(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.anyio
async def test_dictation_turn_propagates_states_and_persists_history():
    tray = RecordingTray()
    with daemon_env("deploy the cluster") as env:
        task = await start_daemon(tray)
        loop, queue, history, main_task = tray.attached
        assert loop is asyncio.get_running_loop()
        assert main_task is task
        assert isinstance(history, HistoryDB)

        await queue.put("toggle")
        await wait_for(lambda: tray.states == ["RECORDING"])
        await queue.put("toggle")
        await wait_for(lambda: tray.states == ["RECORDING", "PROCESSING", "IDLE"])

        env["paste"].assert_called_once_with("deploy the cluster", AppType.EDITOR)
        [rec] = history.search()
        assert (rec.text, rec.app_type, rec.transcription_mode) == ("deploy the cluster", "EDITOR", "batch")
        assert rec.duration_seconds == pytest.approx(0.5)
        assert tray.history_changes == 1

        await stop_daemon(task)
    assert tray.stopped is True


@pytest.mark.anyio
async def test_empty_transcript_is_not_persisted_or_announced():
    tray = RecordingTray()
    with daemon_env("   ") as env:
        task = await start_daemon(tray)
        _, queue, history, _ = tray.attached
        await queue.put("toggle")
        await queue.put("toggle")
        await wait_for(lambda: tray.states[-1:] == ["IDLE"] and len(tray.states) == 3)
        env["paste"].assert_not_called()
        assert history.search() == []
        assert tray.history_changes == 0
        await stop_daemon(task)


# -- API key ------------------------------------------------------------------------

KEY = "sk-test-dummy-0001"


def keyless(mode="batch"):
    return Config(mode=mode, attenuation_enabled=False)


@pytest.mark.anyio
async def test_without_a_key_the_window_opens_and_the_hotkey_does_not_record():
    tray = RecordingTray()
    with daemon_env() as env:
        task = await start_daemon(tray, keyless())
        _, queue, _, _ = tray.attached
        assert tray.key_windows == 1
        await queue.put("toggle")
        await wait_for(lambda: tray.key_windows == 2)
        env["recorder"].start.assert_not_called()
        env["sounds"].play.assert_any_call("error")
        assert tray.states == []
        await stop_daemon(task)


@pytest.mark.anyio
async def test_saved_key_takes_effect_without_a_restart():
    tray, config = RecordingTray(), keyless()
    with daemon_env():
        task = await start_daemon(tray, config)
        _, queue, _, _ = tray.attached
        keystore.set_api_key(KEY)
        await queue.put("api_key")
        await wait_for(lambda: config.openai_api_key == KEY and tray.key_changes == 1)
        await queue.put("toggle")
        await wait_for(lambda: tray.states == ["RECORDING"])
        await stop_daemon(task)


@pytest.mark.anyio
async def test_removed_key_stops_dictation():
    tray, config = RecordingTray(), keyless()
    config.openai_api_key = KEY
    with daemon_env() as env:
        task = await start_daemon(tray, config)
        _, queue, _, _ = tray.attached
        assert tray.key_windows == 0
        await queue.put("api_key")  # the window removed it: nothing is saved
        await wait_for(lambda: config.openai_api_key == "")
        await queue.put("toggle")
        await wait_for(lambda: tray.key_windows == 1)
        env["recorder"].start.assert_not_called()
        await stop_daemon(task)


@pytest.mark.anyio
async def test_hotkey_picks_up_a_key_saved_since_start():
    tray, config = RecordingTray(), keyless()
    with daemon_env():
        task = await start_daemon(tray, config)
        _, queue, _, _ = tray.attached
        keystore.set_api_key(KEY)
        await queue.put("toggle")
        await wait_for(lambda: tray.states == ["RECORDING"])
        assert config.openai_api_key == KEY
        await stop_daemon(task)


@pytest.mark.anyio
async def test_unreadable_keychain_never_asks_for_a_new_key(memory_keyring, monkeypatch):
    def refuse(*_args):
        raise RuntimeError("Failed to unlock the collection!")

    monkeypatch.setattr(memory_keyring, "get_password", refuse)
    tray, config = RecordingTray(), keyless()
    config.api_key_error = "Failed to unlock the collection!"
    with daemon_env() as env:
        task = await start_daemon(tray, config)
        _, queue, _, _ = tray.attached
        await queue.put("toggle")
        await wait_for(lambda: tray.key_changes == 1)  # the hotkey tried the keychain again
        await wait_for(lambda: ("error",) in [c.args for c in env["sounds"].play.call_args_list])
        assert tray.key_windows == 0
        assert config.api_key_error == "Failed to unlock the collection!"
        env["recorder"].start.assert_not_called()
        await stop_daemon(task)


@pytest.mark.anyio
async def test_local_transcription_needs_no_key():
    tray = RecordingTray()
    with daemon_env(), patch("vox.daemon.WhisperCppTranscriber", MagicMock()):
        task = await start_daemon(tray, keyless("whisper_cpp"))
        _, queue, _, _ = tray.attached
        assert tray.key_windows == 0
        await queue.put("toggle")
        await wait_for(lambda: tray.states == ["RECORDING"])
        await stop_daemon(task)


@pytest.mark.anyio
async def test_cancel_returns_tray_to_idle():
    tray = RecordingTray()
    with daemon_env():
        task = await start_daemon(tray)
        _, queue, _, _ = tray.attached
        await queue.put("toggle")
        await queue.put("cancel")
        await wait_for(lambda: tray.states == ["RECORDING", "IDLE"])
        await stop_daemon(task)


@pytest.mark.anyio
async def test_pause_blocks_hotkey_and_resume_restores_it():
    tray = RecordingTray()
    with daemon_env() as env:
        task = await start_daemon(tray)
        _, queue, _, _ = tray.attached

        await queue.put("pause")
        await wait_for(lambda: tray.paused == [True])
        env["sounds"].play.assert_called_with("pause")

        await queue.put("toggle")
        await wait_for(lambda: env["sounds"].play.call_args.args == ("busy",))
        env["recorder"].start.assert_not_called()
        assert tray.states == []

        await queue.put("resume")
        await wait_for(lambda: tray.paused == [True, False])
        env["sounds"].play.assert_called_with("resume")

        await queue.put("toggle")
        await wait_for(lambda: tray.states == ["RECORDING"])
        env["recorder"].start.assert_called_once()
        await stop_daemon(task)


@pytest.mark.anyio
async def test_pause_during_recording_discards_it():
    tray = RecordingTray()
    with daemon_env() as env:
        task = await start_daemon(tray)
        _, queue, _, _ = tray.attached
        await queue.put("toggle")
        await wait_for(lambda: tray.states == ["RECORDING"])

        await queue.put("pause")
        await wait_for(lambda: tray.states == ["RECORDING", "IDLE"])
        assert tray.paused == [True]
        env["recorder"].discard.assert_called_once()
        env["recorder"].stop.assert_not_called()
        env["sounds"].play.assert_called_with("cancel")
        env["paste"].assert_not_called()
        await stop_daemon(task)


@pytest.mark.anyio
async def test_menu_to_daemon_to_icon_round_trip():
    """Real TrayManager: menu click -> daemon queue -> daemon state -> icon."""
    pytest.importorskip("pystray")
    from tests.test_tray import FakeIcon, find, immediate
    from vox.ui.tray import TrayManager

    tray = TrayManager(Config(openai_api_key="test"), icon_factory=FakeIcon, dispatch=immediate)
    icon = tray._icon
    with daemon_env("round trip") as env:
        task = await start_daemon(tray)

        find(icon.menu, "Pause Dictation")(icon)
        await wait_for(lambda: icon.title == "Vox · Paused")

        find(icon.menu, "Pause Dictation")(icon)
        await wait_for(lambda: icon.title == "Vox · Idle")

        titles = []
        tray_render = tray._render

        def spy():
            tray_render()
            titles.append(icon.title)

        tray._render = spy
        await tray._queue.put("toggle")
        await wait_for(lambda: titles == ["Vox · Recording…"])
        await tray._queue.put("toggle")
        await wait_for(lambda: titles == ["Vox · Recording…", "Vox · Processing…", "Vox · Idle"])
        assert "“round trip”" in [item.text for item in icon.menu]  # recent list refreshed

        find(icon.menu, "Quit Vox")(icon)
        await wait_for(task.done)
    assert icon.stopped is True


@pytest.mark.anyio
async def test_transcription_menu_switches_provider_and_persists_choice(tmp_path):
    pytest.importorskip("pystray")
    from tests.test_tray import FakeIcon, find, immediate
    from vox.ui.tray import TrayManager

    binary = tmp_path / "whisper-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[transcription]\nmode = "batch"\n'
        f'[whisper_cpp]\nbinary = "{binary}"\nmodel = "{model}"\n'
    )
    config = Config(
        mode="batch", openai_api_key="test", attenuation_enabled=False,
        whisper_cpp_binary=str(binary), whisper_cpp_model=str(model),
    )
    config._config_path = config_path
    tray = TrayManager(config, icon_factory=FakeIcon, dispatch=immediate)
    icon = tray._icon

    class FakeLocal:
        def __init__(self, config):
            pass

        async def transcribe(self, wav_data, context):
            return "local text"

    with daemon_env("API text") as env, patch("vox.daemon.WhisperCppTranscriber", FakeLocal):
        task = await start_daemon(tray, config)
        menu = find(icon.menu, "Transcription").submenu
        assert find(menu, "Local (whisper.cpp)").enabled is True
        find(menu, "Local (whisper.cpp)")(icon)
        await wait_for(lambda: config.mode == "whisper_cpp")
        assert find(menu, "Local (whisper.cpp)").checked is True
        assert '[transcription]\nmode = "whisper_cpp"' in config_path.read_text()
        assert load_config(config_path).whisper_cpp_model == str(model)

        await tray._queue.put("toggle")
        await wait_for(lambda: icon.title == "Vox · Recording…")
        await tray._queue.put("mode:batch")  # a queued switch cannot interrupt a recording
        await asyncio.sleep(0)
        assert config.mode == "whisper_cpp"
        await tray._queue.put("toggle")
        await wait_for(lambda: icon.title == "Vox · Idle")
        assert tray._history.recent(1)[0].transcription_mode == "whisper_cpp"

        find(menu, "OpenAI (batch)")(icon)
        await wait_for(lambda: config.mode == "batch")
        assert find(menu, "OpenAI (batch)").checked is True
        assert '[transcription]\nmode = "batch"' in config_path.read_text()
        assert load_config(config_path).whisper_cpp_model == str(model)

        await tray._queue.put("toggle")
        await wait_for(lambda: icon.title == "Vox · Recording…")
        await tray._queue.put("toggle")
        await wait_for(lambda: icon.title == "Vox · Idle")
        assert [(rec.text, rec.transcription_mode) for rec in tray._history.recent(2)] == [
            ("API text", "batch"), ("local text", "whisper_cpp"),
        ]
        env["batch"].transcribe.assert_awaited_once()
        await stop_daemon(task)


# -- Headless fallback -----------------------------------------------------


@contextmanager
def linux(display=":1", pystray_module=None):
    """Pretend to be a Linux session, optionally with a stand-in pystray backend."""
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    if display:
        env["DISPLAY"] = display
    modules = {} if pystray_module is None else {"pystray": pystray_module}
    with patch.object(sys, "platform", "linux"), patch.dict(os.environ, env, clear=True), \
         patch.dict(sys.modules, modules):
        yield


def _fake_pystray(has_menu, backend="pystray._appindicator"):
    real = pytest.importorskip("pystray")
    from tests.test_tray import FakeIcon

    icon_class = type("Icon", (FakeIcon,), {"HAS_MENU": has_menu, "__module__": backend})
    return SimpleNamespace(Icon=icon_class, Menu=real.Menu, MenuItem=real.MenuItem)


def test_linux_without_display_runs_headless(caplog):
    from vox.ui.tray import create_tray

    caplog.set_level("INFO")
    with linux(display=None):
        assert create_tray(Config()) is None
    assert "No graphical session" in caplog.text


def test_linux_without_appindicator_runs_headless(caplog):
    """Without python3-gi, pystray falls back to a bare X11 icon with no menu."""
    from vox.ui.tray import create_tray

    with linux(pystray_module=_fake_pystray(has_menu=False, backend="pystray._xorg")):
        assert create_tray(Config()) is None
    assert "python3-gi" in caplog.text


def test_linux_tray_import_failure_runs_headless(caplog):
    from vox.ui.tray import create_tray

    with linux(), patch.dict(sys.modules, {"pystray": None}):
        assert create_tray(Config()) is None
    assert "running headless" in caplog.text


@pytest.mark.parametrize("host", [True, False])
def test_linux_appindicator_tray_uses_light_icons_and_main_loop_dispatch(caplog, host):
    from vox.ui import tray
    from vox.ui.icons import IconState, make_icon

    caplog.set_level("INFO")
    with linux(pystray_module=_fake_pystray(has_menu=True)), patch.object(tray, "_has_tray_host", return_value=host):
        manager = tray.create_tray(Config())
    assert isinstance(manager, tray.TrayManager)
    assert manager._dispatch is tray._glib_dispatch
    assert manager._icon.icon.tobytes() == make_icon(IconState.IDLE, light=True).tobytes()
    assert ("No system tray host" in caplog.text) is (not host)  # icon still created; it shows once a host starts


def test_glib_dispatch_runs_once_on_main_loop_and_logs_errors(caplog):
    pytest.importorskip("gi")
    from gi.repository import GLib

    from vox.ui.tray import _glib_dispatch

    calls = []
    _glib_dispatch(lambda *a: calls.append(a) or True, 1, 2)  # a truthy return must not repeat it
    _glib_dispatch(lambda: 1 / 0)
    assert calls == []  # deferred to the main loop
    context = GLib.MainContext.default()
    for _ in range(5):
        context.iteration(False)
    assert calls == [(1, 2)]
    assert "Tray update failed" in caplog.text


def test_macos_without_gui_session_runs_headless(caplog):
    from vox.ui import tray

    caplog.set_level("INFO")
    with patch.object(sys, "platform", "darwin"), patch.object(tray, "_has_gui_session", return_value=False):
        assert tray.create_tray(Config()) is None
    assert "No GUI session" in caplog.text


def test_gui_session_check_without_quartz():
    from vox.ui import tray

    with patch.dict(sys.modules, {"Quartz": None}):
        assert tray._has_gui_session() is False


def test_tray_construction_failure_runs_headless(caplog):
    from vox.ui import tray

    with patch.object(sys, "platform", "darwin"), \
         patch.object(tray, "_has_gui_session", return_value=True), \
         patch.dict(sys.modules, {"pystray": None}):
        assert tray.create_tray(Config()) is None
    assert "running headless" in caplog.text


def test_run_without_tray_keeps_event_loop_on_main_thread():
    import threading

    from vox import daemon

    seen = {}

    async def fake_main(config, tray=None):
        seen["thread"] = threading.current_thread()
        seen["tray"] = tray

    with patch("vox.ui.tray.create_tray", return_value=None), patch.object(daemon, "_main", fake_main):
        daemon.run(Config(openai_api_key="test"))
    assert seen == {"thread": threading.main_thread(), "tray": None}


def test_run_headless_without_a_key_exits_with_an_error(caplog):
    from vox import daemon

    main = MagicMock()
    with patch("vox.ui.tray.create_tray", return_value=None), patch.object(daemon, "_main", main), \
            pytest.raises(SystemExit) as exit_info:
        daemon.run(Config())
    assert exit_info.value.code == 1
    main.assert_not_called()
    assert "Set API Key" in caplog.text


def test_run_headless_without_a_key_is_fine_for_local_transcription():
    from vox import daemon

    seen = {}

    async def fake_main(config, tray=None):
        seen["ran"] = True

    with patch("vox.ui.tray.create_tray", return_value=None), patch.object(daemon, "_main", fake_main):
        daemon.run(Config(mode="whisper_cpp"))
    assert seen == {"ran": True}


def test_run_with_tray_moves_event_loop_off_main_thread():
    import threading

    from vox import daemon

    seen = {}
    tray = MagicMock()

    async def fake_main(config, t=None):
        seen["thread"] = threading.current_thread().name
        seen["tray"] = t

    with patch("vox.ui.tray.create_tray", return_value=tray), patch.object(daemon, "_main", fake_main):
        daemon.run(Config())
    assert seen == {"thread": "vox-daemon", "tray": tray}
    tray.run.assert_called_once()
    tray.stop.assert_called()


def test_daemon_crash_is_reraised_on_main_thread():
    from vox import daemon

    async def failing_main(config, tray=None):
        raise RuntimeError("no microphone")

    with patch("vox.ui.tray.create_tray", return_value=MagicMock()), patch.object(daemon, "_main", failing_main):
        with pytest.raises(RuntimeError, match="no microphone"):
            daemon.run(Config())
