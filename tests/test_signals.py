"""SIGTERM, how services, logout and upgrades stop Vox, quits the way Quit does, so a lowered volume is restored."""

import asyncio
import contextlib
import os
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

import vox.ui.tray  # noqa: F401  imported now, not first by run() while a test fakes other modules
from vox import daemon as daemon_module
from vox.config import Config
from vox.window import AppContext, AppType


async def idle_reloader(*args):
    await asyncio.sleep(3600)


async def until(condition, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


@contextlib.contextmanager
def sigterm_caught_by(handler):
    """Stand in for SIGTERM's default action, which would end the test run, and put it back afterwards."""
    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextlib.contextmanager
def headless_devices(queues, shutdown_seen):
    """The daemon's microphone, hotkey, sounds, windows and volume, faked."""
    recorder = MagicMock()
    recorder.limit_reached = False
    batch = MagicMock()
    batch.transcribe = AsyncMock(return_value="hello")

    def hotkey(config, loop, queue):
        queues.append(queue)
        listener = MagicMock()
        # What a second SIGTERM would do while the daemon shuts down
        listener.stop.side_effect = lambda: shutdown_seen.append(signal.getsignal(signal.SIGTERM))
        return listener

    with patch("vox.daemon.HotkeyListener", side_effect=hotkey), \
         patch("vox.daemon.Recorder", return_value=recorder), \
         patch("vox.daemon.SoundPlayer"), \
         patch("vox.daemon.Transcriber", return_value=batch), \
         patch("vox.daemon._open_history", return_value=None), \
         patch("vox.daemon._config_reloader", side_effect=idle_reloader), \
         patch("vox.daemon._platform_notice", return_value=None), \
         patch("vox.daemon.detect_active_window", return_value=AppContext("code", "main.py", AppType.EDITOR)), \
         patch("vox.daemon.start_screen_capture", return_value=None), \
         patch("vox.daemon.get_volume", return_value=0.8), \
         patch("vox.daemon.set_volume") as set_volume:
        yield set_volume


@pytest.mark.anyio
async def test_sigterm_while_recording_headless_quits_and_restores_the_volume():
    queues, shutdown_seen, uncaught = [], [], []
    with sigterm_caught_by(lambda *args: uncaught.append(args)), headless_devices(queues, shutdown_seen) as set_volume:
        task = asyncio.create_task(daemon_module._main(Config(openai_api_key="sk-test", attenuation_level=0.5)))
        await until(lambda: queues)
        queues[0].put_nowait("toggle")
        await until(lambda: set_volume.call_args == call(0.4))

        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(task, 2)

        assert not uncaught
        set_volume.assert_called_with(0.8)  # _shutdown ran
        assert shutdown_seen == [signal.SIG_DFL]  # a second SIGTERM ends a shutdown that hangs


@pytest.fixture
def gui_platform(monkeypatch):
    """Fake GLib (Linux) and PyObjC's MachSignals (macOS); returns what gets registered with them for SIGTERM."""
    registered = {}
    glib = MagicMock(PRIORITY_DEFAULT=0)
    glib.unix_signal_add.side_effect = lambda priority, signum, handler: registered.update(signum=signum, handler=handler)
    mach = MagicMock()
    mach.signal.side_effect = lambda signum, handler: registered.update(signum=signum, handler=handler)
    # setitem, not patch.dict: that would also drop every module first imported meanwhile
    for name, module in {
        "gi": MagicMock(), "gi.repository": MagicMock(GLib=glib),
        "PyObjCTools": MagicMock(MachSignals=mach), "PyObjCTools.MachSignals": mach,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return registered


def run_with_tray(tray):
    async def daemon_thread(config, tray=None):
        pass

    with patch("vox.ui.tray.create_tray", return_value=tray), \
         patch("vox.daemon._platform_notice", return_value=None), \
         patch("vox.daemon._main", side_effect=daemon_thread):
        daemon_module.run(Config(openai_api_key="sk-test"))


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_sigterm_with_a_tray_quits_through_the_gui_loop(gui_platform, monkeypatch, platform):
    monkeypatch.setattr(daemon_module.sys, "platform", platform)
    registered = gui_platform
    tray = MagicMock()
    seen = {}

    def gui_loop():  # SIGTERM arrives while the tray runs
        seen["registered"] = registered.get("signum")
        handler = registered["handler"]
        seen["result"] = handler(signal.SIGTERM) if platform == "darwin" else handler()
        seen["disposition"] = signal.getsignal(signal.SIGTERM)

    tray.run.side_effect = gui_loop
    with sigterm_caught_by(lambda *args: None):
        run_with_tray(tray)

    assert seen["registered"] == signal.SIGTERM  # before the GUI loop started
    tray.request_quit.assert_called()  # the daemon shuts down, then stops the tray
    assert seen["disposition"] == signal.SIG_DFL  # a second SIGTERM ends Vox at once
    if platform == "linux":
        assert seen["result"] is False  # the GLib source is removed


def test_sigterm_ends_vox_at_once_after_the_tray_has_stopped(gui_platform):
    """With the GUI loop gone nothing would run the handler, and a SIGTERM would be lost."""
    with sigterm_caught_by(lambda *args: None):
        run_with_tray(MagicMock())
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
