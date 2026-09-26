"""Unit tests for the menu bar TrayManager using a fake pystray icon."""

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pystray")

from vox.config import Config
from vox.history import HistoryDB
from vox.ui.icons import IconState, make_icon
from vox.ui.tray import HISTORY_WINDOW, RECENT_HEADER, VOCAB_WINDOW, TrayManager, _recent_label, _selected_device

DEVICES = [
    {"name": "MacBook Pro Microphone", "max_input_channels": 1},
    {"name": "MacBook Pro Speakers", "max_input_channels": 0},
    {"name": "USB Audio Interface", "max_input_channels": 2},
]


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
    tray = TrayManager(config or Config(), icon_factory=FakeIcon, dispatch=immediate, **kwargs)
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
    tray = TrayManager(Config(), icon_factory=FakeIcon, dispatch=lambda fn, *a: queued.append((fn, a)))
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


def test_device_submenu_lists_input_devices_with_default_checked():
    with patch("vox.ui.tray.sd.query_devices", return_value=DEVICES):
        _, icon = make_tray(Config(audio_device=None))
        submenu = find(icon.menu, "Input Device").submenu
        names = [(i.text, i.checked) for i in items(submenu)]
    assert names == [
        ("System Default", True),
        ("MacBook Pro Microphone", False),
        ("USB Audio Interface", False),
    ]


@pytest.mark.parametrize("spec", [2, "usb audio"])
def test_device_submenu_checks_configured_device(spec):
    with patch("vox.ui.tray.sd.query_devices", return_value=DEVICES):
        _, icon = make_tray(Config(audio_device=spec))
        checked = [i.text for i in items(find(icon.menu, "Input Device").submenu) if i.checked]
    assert checked == ["USB Audio Interface"]


def test_device_submenu_survives_query_failure():
    with patch("vox.ui.tray.sd.query_devices", side_effect=RuntimeError("PortAudio")):
        _, icon = make_tray()
        assert [i.text for i in items(find(icon.menu, "Input Device").submenu)] == ["System Default"]


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
async def test_selecting_device_updates_config_on_daemon_loop():
    config = Config(audio_device=None)
    with patch("vox.ui.tray.sd.query_devices", return_value=DEVICES):
        tray, icon = make_tray(config)
        tray.attach(asyncio.get_running_loop(), asyncio.Queue(), None, MagicMock())
        find(find(icon.menu, "Input Device").submenu, "USB Audio Interface")(icon)
        assert config.audio_device is None  # applied on the daemon loop, not the UI thread
        await settle()
        assert config.audio_device == 2

        find(find(icon.menu, "Input Device").submenu, "System Default")(icon)
        await settle()
        assert config.audio_device is None


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
        ["Pause Dictation", "Input Device", "Transcription"],
        [RECENT_HEADER, "“three”", "“two”", "“one”"],
        ["Search History…", "Vocabulary & Snippets…"],
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
