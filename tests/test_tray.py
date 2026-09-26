"""Unit tests for the menu bar TrayManager using a fake pystray icon."""

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pystray")

from vox.config import Config
from vox.history import HistoryDB
from vox.ui.icons import IconState, make_icon
from vox.ui.tray import (
    HISTORY_WINDOW,
    KEY_WINDOW,
    RECENT_HEADER,
    SET_KEY,
    VOCAB_WINDOW,
    TrayManager,
    _recent_label,
    _selected_device,
)

DEVICES = [(0, "MacBook Pro Microphone"), (2, "USB Audio Interface")]  # the daemon's input-device snapshot


class FakeIcon:
    def __init__(self, name, icon, title, menu):
        self.name = name
        self.icon = icon
        self.title = title
        self.menu = menu
        self.template = True
        self.visible = False
        self.update_menu_calls = 0
        self.stopped = False

    def update_menu(self):
        self.update_menu_calls += 1

    def run(self, setup=None):
        setup(self)

    def stop(self):
        self.stopped = True


def immediate(fn, *args):
    fn(*args)


def make_tray(config=None, **kwargs):
    kwargs.setdefault("focus", MagicMock())  # keep window tests away from the real window server
    tray = TrayManager(config or Config(openai_api_key="test"), icon_factory=FakeIcon, dispatch=immediate, **kwargs)
    return tray, tray._icon


def items(menu):
    return [item for item in menu if item.text != "- - - -"]


def find(menu, text):
    return next(item for item in items(menu) if item.text == text)


async def settle():
    """Let callbacks scheduled with call_soon_threadsafe run."""
    for _ in range(3):
        await asyncio.sleep(0)


def test_initial_icon_is_idle_template():
    _, icon = make_tray()
    assert icon.icon.tobytes() == make_icon(IconState.IDLE).tobytes()
    assert icon.title == "Vox · Idle"
    assert items(icon.menu)[0].text == "Vox · Idle"
    assert items(icon.menu)[0].enabled is False


@pytest.mark.parametrize(
    "state, title, template",
    [("RECORDING", "Vox · Recording…", False), ("PROCESSING", "Vox · Processing…", False), ("IDLE", "Vox · Idle", True)],
)
def test_set_state_updates_icon_title_and_menu(state, title, template):
    tray, icon = make_tray()
    tray.set_state(state)
    assert icon.icon.tobytes() == make_icon(IconState(state)).tobytes()
    assert icon.template is template
    assert icon.title == title
    assert items(icon.menu)[0].text == title
    assert icon.update_menu_calls >= 1


def test_state_updates_go_through_dispatch():
    """Daemon-thread calls must be marshalled, never applied directly."""
    queued = []
    tray = TrayManager(Config(openai_api_key="test"), icon_factory=FakeIcon, dispatch=lambda fn, *a: queued.append((fn, a)))
    icon = tray._icon
    tray.set_state("RECORDING")
    assert icon.title == "Vox · Idle"  # nothing applied yet
    for fn, args in queued:
        fn(*args)
    assert icon.title == "Vox · Recording…"


def test_paused_shows_paused_icon_only_when_idle():
    tray, icon = make_tray()
    tray.set_paused(True)
    assert icon.title == "Vox · Paused"
    assert icon.icon.tobytes() == make_icon(IconState.PAUSED).tobytes()
    assert find(icon.menu, "Pause Dictation").checked is True

    tray.set_state("PROCESSING")  # e.g. paused while a transcription finishes
    assert icon.title == "Vox · Processing…"
    tray.set_state("IDLE")
    assert icon.title == "Vox · Paused"

    tray.set_paused(False)
    assert icon.title == "Vox · Idle"
    assert find(icon.menu, "Pause Dictation").checked is False


def test_run_makes_icon_visible_via_dispatch():
    tray, icon = make_tray()
    tray.run()
    assert icon.visible is True


@pytest.mark.anyio
async def test_pause_item_sends_pause_then_resume():
    tray, icon = make_tray()
    queue: asyncio.Queue[str] = asyncio.Queue()
    tray.attach(asyncio.get_running_loop(), queue, None, MagicMock())

    find(icon.menu, "Pause Dictation")(icon)
    await settle()
    assert queue.get_nowait() == "pause"

    tray.set_paused(True)  # daemon confirms
    find(icon.menu, "Pause Dictation")(icon)
    await settle()
    assert queue.get_nowait() == "resume"


def test_menu_actions_before_attach_are_ignored():
    tray, icon = make_tray()
    find(icon.menu, "Pause Dictation")(icon)  # no loop yet: must not raise


def device_items(icon):
    return [(i.text, i.checked) for i in items(find(icon.menu, "Input Device").submenu)]


def test_device_submenu_lists_input_devices_with_default_checked():
    tray, icon = make_tray(Config(audio_device=None))
    tray.devices_changed(DEVICES)
    assert device_items(icon) == [
        ("System Default", True),
        ("MacBook Pro Microphone", False),
        ("USB Audio Interface", False),
    ]


@pytest.mark.parametrize("spec", [2, "usb audio", "USB Audio Interface"])
def test_device_submenu_checks_configured_device(spec):
    tray, icon = make_tray(Config(audio_device=spec))
    tray.devices_changed(DEVICES)
    assert [text for text, checked in device_items(icon) if checked] == ["USB Audio Interface"]


@pytest.mark.parametrize("spec, shown", [
    (None, [("System Default", True)]),
    ("USB Audio Interface", [("System Default", False), ("USB Audio Interface", True)]),
    (3, [("System Default", False), ("Device 3", True)]),
])
def test_device_submenu_shows_the_current_device_until_the_first_snapshot(spec, shown):
    _, icon = make_tray(Config(audio_device=spec))
    assert device_items(icon) == shown


def test_device_submenu_follows_the_latest_snapshot_and_never_asks_portaudio():
    with patch("sounddevice.query_devices") as query:
        tray, icon = make_tray(Config(audio_device=None))
        tray.devices_changed(DEVICES)
        tray.devices_changed([(0, "MacBook Pro Microphone"), (1, "AirPods Microphone")])  # plugged in later
        assert [text for text, _ in device_items(icon)] == ["System Default", "MacBook Pro Microphone", "AirPods Microphone"]
    query.assert_not_called()
    assert icon.update_menu_calls >= 2


def test_transcription_submenu_shows_modes_and_availability():
    config = Config(openai_api_key="test")
    tray, icon = make_tray(config)
    modes = items(find(icon.menu, "Transcription").submenu)
    assert [(item.text, item.checked, item.enabled) for item in modes] == [
        ("OpenAI (batch)", True, True),
        ("OpenAI (streaming)", False, True),
        ("Local (whisper.cpp)", False, False),
    ]
    tray.set_state("RECORDING")
    assert all(not item.enabled for item in items(find(icon.menu, "Transcription").submenu))

    config.mode = "whisper_cpp"
    config.openai_api_key = ""
    tray.set_state("IDLE")
    modes = items(find(icon.menu, "Transcription").submenu)
    assert [(item.checked, item.enabled) for item in modes] == [
        (False, False), (False, False), (True, True),
    ]


@pytest.mark.anyio
async def test_transcription_menu_sends_mode_to_daemon():
    tray, icon = make_tray(Config(openai_api_key="test"))
    queue: asyncio.Queue[str] = asyncio.Queue()
    tray.attach(asyncio.get_running_loop(), queue, None, MagicMock())
    find(find(icon.menu, "Transcription").submenu, "OpenAI (streaming)")(icon)
    await settle()
    assert queue.get_nowait() == "mode:streaming"


@pytest.mark.anyio
async def test_selecting_device_stores_its_name_on_daemon_loop():
    config = Config(audio_device=None)
    tray, icon = make_tray(config)
    tray.attach(asyncio.get_running_loop(), asyncio.Queue(), None, MagicMock())
    tray.devices_changed(DEVICES)
    find(find(icon.menu, "Input Device").submenu, "USB Audio Interface")(icon)
    assert config.audio_device is None  # applied on the daemon loop, not the UI thread
    await settle()
    assert config.audio_device == "USB Audio Interface"  # the name survives PortAudio renumbering devices

    tray.devices_changed([(0, "AirPods Microphone"), (1, "USB Audio Interface")])
    assert [text for text, checked in device_items(icon) if checked] == ["USB Audio Interface"]

    find(find(icon.menu, "Input Device").submenu, "System Default")(icon)
    await settle()
    assert config.audio_device is None


# -- Recording limit --------------------------------------------------------------


def limit_items(icon):
    return [(i.text, i.checked) for i in items(find(icon.menu, "Recording Limit").submenu)]


def test_recording_limit_submenu_checks_the_current_limit():
    config = Config(openai_api_key="test", max_recording_seconds=900)
    tray, icon = make_tray(config)
    assert limit_items(icon) == [
        ("5 min", False), ("10 min", False), ("15 min", True), ("30 min", False), ("60 min", False),
    ]
    config.max_recording_seconds = 1800
    tray.limit_changed()
    assert [text for text, checked in limit_items(icon) if checked] == ["30 min"]


def test_recording_limit_from_config_file_is_listed_and_checked():
    _, icon = make_tray(Config(openai_api_key="test", max_recording_seconds=120))
    assert [text for text, _ in limit_items(icon)] == ["2 min", "5 min", "10 min", "15 min", "30 min", "60 min"]
    assert [text for text, checked in limit_items(icon) if checked] == ["2 min"]


@pytest.mark.anyio
async def test_recording_limit_item_sends_the_limit_to_the_daemon():
    tray, icon = make_tray()
    queue: asyncio.Queue[str] = asyncio.Queue()
    tray.attach(asyncio.get_running_loop(), queue, None, MagicMock())
    find(find(icon.menu, "Recording Limit").submenu, "10 min")(icon)
    await settle()
    assert queue.get_nowait() == "limit:600"


@pytest.mark.anyio
async def test_recent_dictations_listed_and_copied(tmp_path):
    history = HistoryDB(tmp_path / "history.db")
    for text in ["first", "second", "third", "fourth " + "x" * 80]:
        history.insert(text)
    copy = MagicMock()
    tray, icon = make_tray(copy=copy)
    tray.attach(asyncio.get_running_loop(), asyncio.Queue(), history, MagicMock())

    labels = [i.text for i in items(icon.menu)]
    start = labels.index(RECENT_HEADER)
    recent = items(icon.menu)[start + 1 : start + 4]
    assert [i.text for i in recent] == [_recent_label("fourth " + "x" * 80), "“third”", "“second”"]
    assert "“first”" not in labels

    recent[0](icon)
    copy.assert_called_once_with("fourth " + "x" * 80)  # full text, not the label

    history.insert("fifth")
    tray.history_changed()
    assert "“fifth”" in [i.text for i in items(icon.menu)]
    history.close()


def test_no_recent_dictations_placeholder():
    _, icon = make_tray()
    placeholder = find(icon.menu, "No dictations yet")
    assert placeholder.enabled is False


def test_copy_failure_is_logged_not_raised(tmp_path):
    history = HistoryDB(tmp_path / "history.db")
    history.insert("hello")
    tray, icon = make_tray(copy=MagicMock(side_effect=RuntimeError("pasteboard")))
    tray.attach(MagicMock(), MagicMock(), history, MagicMock())
    find(icon.menu, "“hello”")(icon)
    history.close()


def test_recent_label_flattens_truncates_and_quotes():
    assert _recent_label("line one\n  line two") == "“line one line two”"
    long = _recent_label("word " * 30)
    assert len(long) <= 50 and long.endswith("…”")


def _sections(menu):
    """Top-level menu texts grouped between separators."""
    groups = [[]]
    for item in menu:
        if item.text == "- - - -":
            groups.append([])
        else:
            groups[-1].append(item.text)
    return groups


@pytest.mark.anyio
async def test_recent_dictations_are_their_own_menu_section(tmp_path):
    history = HistoryDB(tmp_path / "history.db")
    for text in ["one", "two", "three"]:
        history.insert(text)
    tray, icon = make_tray()
    tray.attach(asyncio.get_running_loop(), asyncio.Queue(), history, MagicMock())
    assert _sections(icon.menu) == [
        ["Vox · Idle"],
        ["Pause Dictation", "Input Device", "Transcription", "Recording Limit"],
        [RECENT_HEADER, "“three”", "“two”", "“one”"],
        ["Search History…", "Vocabulary & Snippets…", "Set API Key…"],
        ["Quit Vox"],
    ]
    history.close()


def test_empty_recent_section_keeps_its_place():
    _, icon = make_tray()
    assert _sections(icon.menu)[2] == ["No dictations yet"]


def test_selected_device_resolution():
    devices = [(0, "MacBook Pro Microphone"), (2, "USB Audio Interface")]
    assert _selected_device(None, devices) is None
    assert _selected_device(2, devices) == 2
    assert _selected_device(5, devices) is None
    assert _selected_device("usb", devices) == 2
    assert _selected_device("missing", devices) is None
    # A stored name picks its own device, not an earlier one whose name contains it
    assert _selected_device("USB Audio", [(1, "USB Audio 2"), (4, "USB Audio")]) == 4


@pytest.mark.anyio
async def test_quit_cancels_daemon_main_task():
    tray, icon = make_tray()
    main_task = MagicMock()
    tray.attach(asyncio.get_running_loop(), asyncio.Queue(), None, main_task)
    find(icon.menu, "Quit Vox")(icon)
    await settle()
    main_task.cancel.assert_called_once()
    assert icon.stopped is False  # the daemon stops the tray on its way out


def test_quit_before_attach_stops_tray():
    tray, icon = make_tray()
    find(icon.menu, "Quit Vox")(icon)
    assert icon.stopped is True


def test_stop_is_dispatched():
    tray, icon = make_tray()
    tray.stop()
    assert icon.stopped is True


def _fake_proc(alive=True):
    proc = MagicMock()
    proc.poll.return_value = None if alive else 0
    return proc


def test_history_item_disabled_without_history():
    _, icon = make_tray()
    assert find(icon.menu, "Search History…").enabled is False


def test_history_window_launch_is_single_instance(tmp_path):
    history = HistoryDB(tmp_path / "history.db")
    procs = [_fake_proc(alive=True), _fake_proc(alive=True)]
    launcher, focus = MagicMock(side_effect=procs), MagicMock()
    tray, icon = make_tray(launcher=launcher, focus=focus)
    tray.attach(MagicMock(), MagicMock(), history, MagicMock())
    command = [sys.executable, "-m", HISTORY_WINDOW, "--db", str(history.path)]

    find(icon.menu, "Search History…")(icon)
    launcher.assert_called_once_with(command)
    focus.assert_called_once_with(procs[0], None)

    find(icon.menu, "Search History…")(icon)  # still open: brought forward, not relaunched
    assert launcher.call_count == 1
    focus.assert_called_with(procs[0], command)

    procs[0].poll.return_value = 0  # user closed it
    find(icon.menu, "Search History…")(icon)
    assert launcher.call_count == 2
    focus.assert_called_with(procs[1], None)
    history.close()


def test_vocab_window_gets_config_path(tmp_path):
    config = Config()
    config._config_path = tmp_path / "config.toml"
    launcher = MagicMock(return_value=_fake_proc())
    _, icon = make_tray(config, launcher=launcher)
    find(icon.menu, "Vocabulary & Snippets…")(icon)
    launcher.assert_called_once_with([sys.executable, "-m", VOCAB_WINDOW, "--config", str(tmp_path / "config.toml")])


def test_open_windows_are_closed_when_tray_exits(tmp_path):
    config = Config()
    config._config_path = tmp_path / "config.toml"
    alive, exited = _fake_proc(alive=True), _fake_proc(alive=False)
    tray, icon = make_tray(config, launcher=MagicMock(side_effect=[alive]))
    find(icon.menu, "Vocabulary & Snippets…")(icon)
    tray._windows["exited"] = exited
    tray.run()
    alive.terminate.assert_called_once()
    exited.terminate.assert_not_called()


def test_launch_failure_is_logged_not_raised(tmp_path):
    config = Config()
    config._config_path = tmp_path / "config.toml"
    focus = MagicMock()
    _, icon = make_tray(config, launcher=MagicMock(side_effect=OSError("no python")), focus=focus)
    find(icon.menu, "Vocabulary & Snippets…")(icon)
    focus.assert_not_called()


def test_focus_on_linux_relaunches_only_an_open_window():
    from vox.ui import tray

    proc = _fake_proc()
    with patch.object(tray.sys, "platform", "linux"), patch.object(tray, "_launch_window") as launch:
        tray._focus_window(proc, None)  # just launched: GTK presents it
        launch.assert_not_called()
        tray._focus_window(proc, ["python", "-m", VOCAB_WINDOW])  # the single-instance app presents the open one
        launch.assert_called_once_with(["python", "-m", VOCAB_WINDOW])


def test_focus_on_macos_hands_activation_to_the_window():
    from vox.ui import tray

    proc = _fake_proc()
    with patch.object(tray.sys, "platform", "darwin"), patch.object(tray, "_hand_focus_to") as hand:
        tray._focus_window(proc, ["python"])
    hand.assert_called_once_with(proc)


def test_focus_failure_is_not_raised():
    from vox.ui import tray

    with patch.object(tray.sys, "platform", "darwin"), \
         patch.object(tray, "_hand_focus_to", side_effect=RuntimeError("no window server")):
        tray._focus_window(_fake_proc(), None)


def test_windows_close_even_if_tray_loop_raises(tmp_path):
    config = Config()
    config._config_path = tmp_path / "config.toml"
    proc = _fake_proc(alive=True)
    tray, icon = make_tray(config, launcher=MagicMock(return_value=proc))
    find(icon.menu, "Vocabulary & Snippets…")(icon)
    icon.run = MagicMock(side_effect=RuntimeError("tray backend"))
    with pytest.raises(RuntimeError):
        tray.run()
    proc.terminate.assert_called_once()


def test_linux_icon_tolerates_missing_notification_server():
    from types import SimpleNamespace

    from vox.ui.tray import _linux_icon_class

    class Backend(FakeIcon):
        def _finalize(self):
            raise RuntimeError("org.freedesktop.Notifications was not provided by any .service files")

    icon = _linux_icon_class(SimpleNamespace(Icon=Backend))("vox", None, "Vox", None)
    icon._finalize()  # must not raise, or Quit would exit non-zero and systemd would restart vox


# -- API key ----------------------------------------------------------------------


def test_missing_key_is_the_status_and_the_first_item():
    _, icon = make_tray(Config(mode="batch"))
    assert icon.title == "Vox · API key needed"
    assert [item.text for item in icon.menu][:2] == ["Vox · API key needed", SET_KEY]
    assert [item.text for item in icon.menu].count(SET_KEY) == 1


def test_unreadable_keyring_is_not_reported_as_a_missing_key():
    config = Config(mode="batch")
    config.api_key_error = "Failed to unlock the collection!"
    _, icon = make_tray(config)
    assert icon.title == "Vox · Can’t read the keyring"


def test_with_a_key_the_item_sits_with_the_other_windows():
    _, icon = make_tray()
    texts = [item.text for item in icon.menu]
    assert texts[0] == "Vox · Idle"
    assert texts.index(SET_KEY) == texts.index("Vocabulary & Snippets…") + 1


def test_local_transcription_needs_no_key():
    _, icon = make_tray(Config(mode="whisper_cpp"))
    assert icon.title == "Vox · Idle"


def test_status_follows_the_key_and_the_mode():
    config = Config(mode="batch")
    tray, icon = make_tray(config)
    config.openai_api_key = "test"
    tray.key_changed()
    assert icon.title == "Vox · Idle"
    config.openai_api_key = ""
    config.mode = "whisper_cpp"
    tray.mode_changed()
    assert icon.title == "Vox · Idle"
    config.mode = "streaming"
    tray.mode_changed()
    assert icon.title == "Vox · API key needed"


def test_recording_shows_the_state_not_the_key():
    tray, icon = make_tray(Config(mode="batch"))
    tray.set_state("RECORDING")
    assert icon.title == "Vox · Recording…"


def test_key_window_closing_tells_the_daemon_to_reread_the_key():
    proc = _fake_proc()
    launcher = MagicMock(return_value=proc)
    tray, icon = make_tray(Config(mode="batch"), launcher=launcher)
    loop, queue = MagicMock(), MagicMock()
    tray.attach(loop, queue, None, MagicMock())
    with patch("vox.ui.tray.threading.Thread") as thread:
        find(icon.menu, SET_KEY)(icon)
    launcher.assert_called_once_with([sys.executable, "-m", KEY_WINDOW])
    kwargs = thread.call_args.kwargs
    target, args = kwargs["target"], kwargs["args"]
    target(*args)  # what the thread runs: wait for the window, then tell the daemon
    proc.wait.assert_called_once_with()
    loop.call_soon_threadsafe.assert_called_once_with(queue.put_nowait, "api_key")


def test_daemon_can_open_the_key_window():
    launcher = MagicMock(return_value=_fake_proc())
    tray, _ = make_tray(Config(mode="batch"), launcher=launcher)
    with patch("vox.ui.tray.threading.Thread"):
        tray.open_key_window()
    launcher.assert_called_once_with([sys.executable, "-m", KEY_WINDOW])


# -- Status line problems -----------------------------------------------------------


def test_status_line_reports_the_most_urgent_problem_while_idle():
    config = Config(mode="batch")
    config.mode_error = "whisper.cpp model not found: /models/ggml-base.bin"
    tray, icon = make_tray(config)
    tray.set_notice("Microphone is silent: check its permission")
    assert icon.title == "Vox · API key needed"  # the key first

    config.openai_api_key = "test"
    tray.key_changed()
    assert icon.title == "Vox · whisper.cpp model not found: /models/ggml-base.bin"  # then the mode
    assert items(icon.menu)[0].text == icon.title

    config.mode_error = None
    tray.mode_changed()
    assert icon.title == "Vox · Microphone is silent: check its permission"  # then the notice

    tray.set_state("RECORDING")
    assert icon.title == "Vox · Recording…"
    tray.set_state("IDLE")
    tray.set_notice(None)
    assert icon.title == "Vox · Idle"


def test_a_long_problem_is_shortened_to_one_line():
    config = Config(mode="whisper_cpp")
    config.mode_error = "whisper.cpp model not found:\n" + "/very/long/path" * 10
    _, icon = make_tray(config)
    assert "\n" not in icon.title
    assert len(icon.title) <= len("Vox · ") + 72 and icon.title.endswith("…")


# -- Window processes ---------------------------------------------------------------


def test_every_window_process_is_waited_on_so_none_lingers(tmp_path):
    config = Config()
    config._config_path = tmp_path / "config.toml"
    proc = _fake_proc()
    tray, icon = make_tray(config, launcher=MagicMock(return_value=proc))
    with patch("vox.ui.tray.threading.Thread") as thread:
        find(icon.menu, "Vocabulary & Snippets…")(icon)
    kwargs = thread.call_args.kwargs
    kwargs["target"](*kwargs["args"])  # what the thread runs
    proc.wait.assert_called_once_with()


def test_closing_the_history_window_refreshes_recent_dictations(tmp_path):
    history = HistoryDB(tmp_path / "history.db")
    history.insert("kept")
    history.insert("deleted in the window")
    tray, icon = make_tray(launcher=MagicMock(return_value=_fake_proc()))
    tray.attach(MagicMock(), MagicMock(), history, MagicMock())
    with patch("vox.ui.tray.threading.Thread") as thread:
        find(icon.menu, "Search History…")(icon)
    history.delete(history.recent(1)[0].id)
    kwargs = thread.call_args.kwargs
    kwargs["target"](*kwargs["args"])  # the window exits
    labels = [item.text for item in items(icon.menu)]
    assert "“kept”" in labels and "“deleted in the window”" not in labels
    history.close()


def test_linux_reopen_process_is_waited_on():
    from vox.ui import tray

    reopened = _fake_proc()
    with patch.object(tray.sys, "platform", "linux"), \
         patch.object(tray, "_launch_window", return_value=reopened), \
         patch.object(tray.threading, "Thread") as thread:
        tray._focus_window(_fake_proc(), ["python", "-m", VOCAB_WINDOW])
    assert thread.call_args.kwargs["target"] == reopened.wait
    thread.return_value.start.assert_called_once_with()


# -- macOS menu ---------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="pystray's macOS backend")
def test_darwin_menu_is_not_rebuilt_while_it_is_open():
    """pystray maps a click to the item's tag in the newest callbacks list, so an open menu must keep its own."""
    import pystray._darwin

    from vox.ui import tray

    VoxIcon = tray._darwin_icon_class()
    icon = VoxIcon.__new__(VoxIcon)  # no __init__: never put a real item in the menu bar
    icon._visible = False
    with patch.object(pystray._darwin.Icon, "_update_menu") as rebuild, \
         patch.object(tray, "_menu_is_tracking", return_value=True) as tracking, \
         patch("PyObjCTools.AppHelper.callLater") as later:
        icon.update_menu()
        icon.update_menu()
        rebuild.assert_not_called()  # the open menu and its callbacks stay as they are
        later.assert_called_once()  # one rebuild, however many updates arrived

        tracking.return_value = False  # the menu closed and the clicked item's action ran
        delay, deferred = later.call_args.args
        assert delay > 0  # a delayed call runs only in the default run-loop mode, never during tracking
        deferred()
        rebuild.assert_called_once_with()

        icon.update_menu()
        assert rebuild.call_count == 2  # closed: rebuilt at once
