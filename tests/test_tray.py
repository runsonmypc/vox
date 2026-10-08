"""Unit tests for the menu bar TrayManager using a fake pystray icon."""

import asyncio
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pystray")

from vox.config import Config
from vox.history import HistoryDB
from vox.modes import LABELS
from vox.ui.icons import IconState, make_icon
from vox.ui.tray import (
    CONFIG_ERROR,
    HISTORY_WINDOW,
    RECENT_HEADER,
    SET_KEY,
    SETTINGS,
    SETTINGS_OPEN,
    SETTINGS_WINDOW,
    TrayManager,
    _recent_label,
)


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
    assert icon.title == "Vox Transfer · Idle"
    assert items(icon.menu)[0].text == "Vox Transfer · Idle"
    assert items(icon.menu)[0].enabled is False


@pytest.mark.parametrize(
    "state, title, template",
    [("RECORDING", "Vox Transfer · Recording…", False), ("PROCESSING", "Vox Transfer · Processing…", False), ("IDLE", "Vox Transfer · Idle", True)],
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
    assert icon.title == "Vox Transfer · Idle"  # nothing applied yet
    for fn, args in queued:
        fn(*args)
    assert icon.title == "Vox Transfer · Recording…"


def test_paused_shows_paused_icon_only_when_idle():
    tray, icon = make_tray()
    tray.set_paused(True)
    assert icon.title == "Vox Transfer · Paused"
    assert icon.icon.tobytes() == make_icon(IconState.PAUSED).tobytes()
    assert find(icon.menu, "Pause Dictation").checked is True

    tray.set_state("PROCESSING")  # e.g. paused while a transcription finishes
    assert icon.title == "Vox Transfer · Processing…"
    tray.set_state("IDLE")
    assert icon.title == "Vox Transfer · Paused"

    tray.set_paused(False)
    assert icon.title == "Vox Transfer · Idle"
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
        ["Vox Transfer · Idle"],
        ["Pause Dictation"],
        [RECENT_HEADER, "“three”", "“two”", "“one”"],
        ["History…", "Transcription", SETTINGS],
        ["Quit Vox Transfer"],
    ]
    history.close()


def test_empty_recent_section_keeps_its_place():
    _, icon = make_tray()
    assert _sections(icon.menu)[2] == ["No dictations yet"]


def test_a_missing_key_puts_set_api_key_under_the_status_line():
    _, icon = make_tray(Config(mode="batch"))
    assert _sections(icon.menu) == [
        ["Vox Transfer · API key needed", SET_KEY],
        ["Pause Dictation"],
        ["No dictations yet"],
        ["History…", "Transcription", SETTINGS],
        ["Quit Vox Transfer"],
    ]


def test_transcription_is_the_only_settings_submenu_and_there_are_no_separate_windows():
    _, icon = make_tray()
    texts = [item.text for item in items(icon.menu)]
    for gone in ("Input Device", "Recording Limit", "Vocabulary & Snippets…", "Set Hotkey…", SET_KEY):
        assert gone not in texts
    assert [item.text for item in items(icon.menu) if item.submenu is not None] == ["Transcription"]


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


def test_transcription_submenu_follows_vox_modes():
    problems = {"batch": "Broken setup", "streaming": "Set an OpenAI API key first", "whisper_cpp": None}
    _, icon = make_tray(Config(mode="batch", openai_api_key="test"))
    with patch("vox.ui.tray.mode_problem", side_effect=lambda config, mode: problems[mode]) as problem:
        modes = items(find(icon.menu, "Transcription").submenu)
        assert [(item.text, item.enabled) for item in modes] == [
            (LABELS["batch"], True),  # the current mode stays clickable: picking it again retries its setup
            (LABELS["streaming"], False),
            (LABELS["whisper_cpp"], True),
        ]
    assert {call.args[1] for call in problem.call_args_list} == {"streaming", "whisper_cpp"}


def test_transcription_submenu_waits_while_settings_is_open():
    tray, icon = make_tray(Config(openai_api_key="test"))
    tray._apply_settings_open(True)
    assert all(not item.enabled for item in items(find(icon.menu, "Transcription").submenu))
    tray._apply_settings_open(False)
    assert find(find(icon.menu, "Transcription").submenu, "OpenAI (streaming)").enabled


def test_transcription_submenu_waits_for_the_settings_file_to_load():
    config = Config(openai_api_key="test")
    config.config_error = "Invalid config.toml"
    tray, icon = make_tray(config)
    assert all(not item.enabled for item in items(find(icon.menu, "Transcription").submenu))
    config.config_error = None
    tray.mode_changed()
    assert find(find(icon.menu, "Transcription").submenu, "OpenAI (streaming)").enabled


@pytest.mark.anyio
async def test_transcription_menu_sends_mode_to_daemon():
    tray, icon = make_tray(Config(openai_api_key="test"))
    queue: asyncio.Queue[str] = asyncio.Queue()
    tray.attach(asyncio.get_running_loop(), queue, None, MagicMock())
    find(find(icon.menu, "Transcription").submenu, "OpenAI (streaming)")(icon)
    await settle()
    assert queue.get_nowait() == "mode:streaming"


@pytest.mark.anyio
async def test_quit_cancels_daemon_main_task():
    tray, icon = make_tray()
    main_task = MagicMock()
    tray.attach(asyncio.get_running_loop(), asyncio.Queue(), None, main_task)
    find(icon.menu, "Quit Vox Transfer")(icon)
    await settle()
    main_task.cancel.assert_called_once()
    assert icon.stopped is False  # the daemon stops the tray on its way out


def test_quit_before_attach_stops_tray():
    tray, icon = make_tray()
    find(icon.menu, "Quit Vox Transfer")(icon)
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
    assert find(icon.menu, "History…").enabled is False


def test_history_window_launch_is_single_instance(tmp_path):
    history = HistoryDB(tmp_path / "history.db")
    procs = [_fake_proc(alive=True), _fake_proc(alive=True)]
    launcher, focus = MagicMock(side_effect=procs), MagicMock()
    tray, icon = make_tray(launcher=launcher, focus=focus)
    tray.attach(MagicMock(), MagicMock(), history, MagicMock())
    command = [sys.executable, "-P", "-m", HISTORY_WINDOW, "--db", str(history.path)]

    find(icon.menu, "History…")(icon)
    launcher.assert_called_once_with(command)
    focus.assert_called_once_with(procs[0], None)

    find(icon.menu, "History…")(icon)  # still open: brought forward, not relaunched
    assert launcher.call_count == 1
    focus.assert_called_with(procs[0], command)

    procs[0].poll.return_value = 0  # user closed it
    find(icon.menu, "History…")(icon)
    assert launcher.call_count == 2
    focus.assert_called_with(procs[1], None)
    history.close()


def test_settings_window_gets_config_path_and_opens_on_general(tmp_path):
    config = Config()
    config._config_path = tmp_path / "config.toml"
    launcher = MagicMock(return_value=_fake_proc())
    _, icon = make_tray(config, launcher=launcher)
    find(icon.menu, SETTINGS)(icon)
    launcher.assert_called_once_with([sys.executable, "-P", "-m", SETTINGS_WINDOW, "--config", str(tmp_path / "config.toml")])


def test_open_windows_are_closed_when_tray_exits(tmp_path):
    config = Config()
    config._config_path = tmp_path / "config.toml"
    alive, exited = _fake_proc(alive=True), _fake_proc(alive=False)
    tray, icon = make_tray(config, launcher=MagicMock(side_effect=[alive]))
    find(icon.menu, SETTINGS)(icon)
    tray._windows["exited"] = exited
    tray.run()
    alive.terminate.assert_called_once()
    exited.terminate.assert_not_called()


def test_launch_failure_is_logged_not_raised(tmp_path):
    config = Config()
    config._config_path = tmp_path / "config.toml"
    focus = MagicMock()
    _, icon = make_tray(config, launcher=MagicMock(side_effect=OSError("no python")), focus=focus)
    find(icon.menu, SETTINGS)(icon)
    focus.assert_not_called()


def test_focus_on_linux_relaunches_only_an_open_window():
    from vox.ui import tray

    proc = _fake_proc()
    with patch.object(tray.sys, "platform", "linux"), patch.object(tray, "_launch_window") as launch:
        tray._focus_window(proc, None)  # just launched: GTK presents it
        launch.assert_not_called()
        tray._focus_window(proc, ["python", "-m", SETTINGS_WINDOW])  # the single-instance app presents the open one
        launch.assert_called_once_with(["python", "-m", SETTINGS_WINDOW])


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
    find(icon.menu, SETTINGS)(icon)
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

    icon = _linux_icon_class(SimpleNamespace(Icon=Backend))("vox", None, "Vox Transfer", None)
    icon._finalize()  # must not raise, or Quit would exit non-zero and systemd would restart vox


# -- API key ----------------------------------------------------------------------


def test_missing_key_is_the_status_and_the_first_item():
    _, icon = make_tray(Config(mode="batch"))
    assert icon.title == "Vox Transfer · API key needed"
    assert [item.text for item in icon.menu][:2] == ["Vox Transfer · API key needed", SET_KEY]
    assert [item.text for item in icon.menu].count(SET_KEY) == 1


def test_unreadable_keyring_is_not_reported_as_a_missing_key():
    config = Config(mode="batch")
    config.api_key_error = "Failed to unlock the collection!"
    _, icon = make_tray(config)
    assert icon.title == "Vox Transfer · Can’t read the keyring"


def test_local_transcription_needs_no_key():
    _, icon = make_tray(Config(mode="whisper_cpp"))
    assert icon.title == "Vox Transfer · Idle"


def test_status_follows_the_key_and_the_mode():
    config = Config(mode="batch")
    tray, icon = make_tray(config)
    config.openai_api_key = "test"
    tray.key_changed()
    assert icon.title == "Vox Transfer · Idle"
    config.openai_api_key = ""
    config.mode = "whisper_cpp"
    tray.mode_changed()
    assert icon.title == "Vox Transfer · Idle"
    config.mode = "streaming"
    tray.mode_changed()
    assert icon.title == "Vox Transfer · API key needed"


def test_recording_shows_the_state_not_the_key():
    tray, icon = make_tray(Config(mode="batch"))
    tray.set_state("RECORDING")
    assert icon.title == "Vox Transfer · Recording…"


def settings_tray(tmp_path, launcher, config=None):
    """A tray with a config path whose daemon events, and window launches, land in one list in order."""
    config = config or Config(openai_api_key="test")
    config._config_path = tmp_path / "config.toml"
    events = []
    tray, icon = make_tray(config, launcher=launcher)
    loop, queue = MagicMock(), MagicMock()
    loop.call_soon_threadsafe.side_effect = lambda fn, *args: events.append(args[0])
    tray.attach(loop, queue, None, MagicMock())
    return tray, icon, events


def test_set_api_key_opens_settings_on_the_transcription_page(tmp_path):
    launcher = MagicMock(return_value=_fake_proc())
    _, icon, _ = settings_tray(tmp_path, launcher, Config(mode="batch"))
    with patch("vox.ui.tray.threading.Thread"):
        find(icon.menu, SET_KEY)(icon)
    launcher.assert_called_once_with(
        [sys.executable, "-P", "-m", SETTINGS_WINDOW, "--config", str(tmp_path / "config.toml"), "--page", "transcription"]
    )


def test_daemon_can_open_settings_on_a_page(tmp_path):
    launcher = MagicMock(return_value=_fake_proc())
    tray, _, events = settings_tray(tmp_path, launcher, Config(mode="batch"))
    with patch("vox.ui.tray.threading.Thread"):
        tray.open_settings("transcription")
    assert launcher.call_args.args[0][-2:] == ["--page", "transcription"]
    assert events == ["settings:open"]


# -- Settings -------------------------------------------------------------------------


def test_settings_window_suspends_dictation_until_it_closes(tmp_path):
    proc = _fake_proc()

    def launch(command):
        events.append("launched")
        return proc

    launcher = MagicMock(side_effect=launch)
    tray, icon, events = settings_tray(tmp_path, launcher)
    with patch("vox.ui.tray.threading.Thread") as thread:
        find(icon.menu, SETTINGS)(icon)
    command = [sys.executable, "-P", "-m", SETTINGS_WINDOW, "--config", str(tmp_path / "config.toml")]
    launcher.assert_called_once_with(command)
    assert events == ["settings:open", "launched"]
    assert icon.title == f"Vox Transfer · {SETTINGS_OPEN}"

    kwargs = thread.call_args.kwargs
    kwargs["target"](*kwargs["args"])  # what the thread runs: wait for the window, however it ends, then resume
    proc.wait.assert_called_once_with()
    assert events == ["settings:open", "launched", "settings:closed"]
    assert icon.title == "Vox Transfer · Idle"


def test_a_settings_window_that_fails_to_open_resumes_dictation(tmp_path):
    tray, icon, events = settings_tray(tmp_path, MagicMock(side_effect=OSError("no python")))
    find(icon.menu, SETTINGS)(icon)
    assert events == ["settings:open", "settings:closed"]
    assert icon.title == "Vox Transfer · Idle"


def test_an_open_settings_window_is_brought_forward_without_suspending_again(tmp_path):
    proc = _fake_proc(alive=True)
    launcher = MagicMock(return_value=proc)
    tray, icon, events = settings_tray(tmp_path, launcher, Config(mode="batch"))
    with patch("vox.ui.tray.threading.Thread"):
        find(icon.menu, SETTINGS)(icon)
        tray.open_settings("transcription")  # the hotkey pressed without a key while Settings is open
    assert launcher.call_count == 1
    tray._focus.assert_called_with(proc, [*launcher.call_args.args[0], "--page", "transcription"])
    assert events == ["settings:open"]


def test_settings_is_unavailable_while_recording_or_processing():
    tray, icon = make_tray()
    assert find(icon.menu, SETTINGS).enabled
    for state, enabled in (("RECORDING", False), ("PROCESSING", False), ("IDLE", True)):
        tray.set_state(state)
        assert find(icon.menu, SETTINGS).enabled is enabled
    tray.set_paused(True)
    assert find(icon.menu, SETTINGS).enabled


# -- Status line problems -----------------------------------------------------------


def test_status_line_reports_the_most_urgent_problem_while_idle():
    config = Config(mode="batch")
    config.config_error = "Invalid config.toml: Expected '=' after a key in a key/value pair (at line 3, column 7)"
    config.mode_error = "whisper.cpp model not found: /models/ggml-base.bin"
    tray, icon = make_tray(config)
    tray.set_notice("Microphone is silent: check its permission")
    # config.toml first: until it loads, the mode, and whether it needs a key, is only the default's.
    # The parser's message is in the log.
    assert icon.title == f"Vox Transfer · {CONFIG_ERROR}"
    assert items(icon.menu)[0].text == icon.title

    config.config_error = None  # the reloader read the fixed file and told the tray
    tray.mode_changed()
    assert icon.title == "Vox Transfer · API key needed"  # then the key

    config.openai_api_key = "test"
    tray.key_changed()
    assert icon.title == "Vox Transfer · whisper.cpp model not found: /models/ggml-base.bin"  # then the mode
    assert items(icon.menu)[0].text == icon.title

    tray._apply_settings_open(True)
    assert icon.title == "Vox Transfer · whisper.cpp model not found: /models/ggml-base.bin"  # more urgent than Settings

    config.mode_error = None
    tray.mode_changed()
    assert icon.title == f"Vox Transfer · {SETTINGS_OPEN}"  # then Settings being open, which silences the hotkey

    tray._apply_settings_open(False)
    assert icon.title == "Vox Transfer · Microphone is silent: check its permission"  # then the notice

    tray.set_state("RECORDING")
    assert icon.title == "Vox Transfer · Recording…"
    tray.set_state("IDLE")
    tray.set_notice(None)
    assert icon.title == "Vox Transfer · Idle"


def test_a_settings_file_error_shows_while_paused_but_not_while_processing():
    config = Config(openai_api_key="test")
    config.config_error = "Invalid config.toml"
    tray, icon = make_tray(config)
    assert icon.title == f"Vox Transfer · {CONFIG_ERROR}"
    tray.set_paused(True)
    assert icon.title == f"Vox Transfer · {CONFIG_ERROR}"
    tray.set_paused(False)
    tray.set_state("PROCESSING")
    assert icon.title == "Vox Transfer · Processing…"


def test_a_long_problem_is_shortened_to_one_line():
    config = Config(mode="whisper_cpp")
    config.mode_error = "whisper.cpp model not found:\n" + "/very/long/path" * 10
    _, icon = make_tray(config)
    assert "\n" not in icon.title
    assert len(icon.title) <= len("Vox Transfer · ") + 72 and icon.title.endswith("…")


# -- Window processes ---------------------------------------------------------------


def test_windows_ignore_a_vox_folder_in_the_working_directory(tmp_path):
    (tmp_path / "vox").mkdir()
    (tmp_path / "vox" / "__init__.py").write_text("raise SystemExit('shadowed by the working directory')\n")
    launcher = MagicMock(return_value=_fake_proc())
    _, icon = make_tray(launcher=launcher)
    find(icon.menu, SETTINGS)(icon)
    command = launcher.call_args.args[0]
    interpreter = command[: command.index("-m")]  # the interpreter and its flags, as the window runs them
    probe = subprocess.run(
        [*interpreter, "-c", "import vox"], cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert probe.returncode == 0, probe.stderr


def test_every_window_process_is_waited_on_so_none_lingers(tmp_path):
    config = Config()
    config._config_path = tmp_path / "config.toml"
    proc = _fake_proc()
    tray, icon = make_tray(config, launcher=MagicMock(return_value=proc))
    with patch("vox.ui.tray.threading.Thread") as thread:
        find(icon.menu, SETTINGS)(icon)
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
        find(icon.menu, "History…")(icon)
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
        tray._focus_window(_fake_proc(), ["python", "-m", SETTINGS_WINDOW])
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
