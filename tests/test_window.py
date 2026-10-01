"""Tests for active window detection, app classification and focused-window screen context.

All platform tools and frameworks are faked: nothing captures the screen or queries X11.
"""

from __future__ import annotations

import contextlib
import itertools
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vox import _atspi_reader, window
from vox.config import Config
from vox.window import AppContext, AppType, _classify


class FakeTools:
    """subprocess.run stand-in: argv prefix -> stdout bytes, or an exception to raise."""

    def __init__(self, responses: dict[tuple[str, ...], bytes | Exception]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def run(self, args, **kwargs):
        assert isinstance(args, list), "commands must be argv lists, never shell strings"
        assert not kwargs.get("shell")
        assert kwargs.get("timeout"), f"{args[0]} runs without a timeout"
        self.calls.append(list(args))
        for prefix, response in self.responses.items():
            if tuple(args[:len(prefix)]) == prefix:
                if isinstance(response, Exception):
                    raise response
                return subprocess.CompletedProcess(args, 0, stdout=response, stderr=b"")
        raise subprocess.CalledProcessError(1, args)

    def called(self, *prefix: str) -> bool:
        return any(tuple(call[:len(prefix)]) == prefix for call in self.calls)


# -- Linux window detection ----------------------------------------------------------


def _x11_tools(title: bytes, xprop: bytes = b'WM_CLASS(STRING) = "kitty", "kitty"\n_NET_WM_PID(CARDINAL) = 4242\n'):
    return FakeTools({
        ("xdotool", "getactivewindow"): b"83886090\n",
        ("xprop", "-id", "83886090"): xprop,
        ("xdotool", "getwindowname", "83886090"): title + b"\n",
    })


@pytest.mark.parametrize("title", [
    "Docs\n1\n1;id>/tmp/pwn # - Google Chrome",
    "a\x0bb\x0bc",
    "a\x85b\x85c",
    "a b c",
    "",
])
def test_linux_title_can_never_shift_window_id_or_pid(title):
    """Line breaks in a (possibly attacker-set) title stay in the title."""
    tools = _x11_tools(title.encode())
    with patch("vox.window.subprocess.run", tools.run):
        ctx = window._detect_active_window_linux(Config())
    assert (ctx.win_id, ctx.pid, ctx.wm_class, ctx.app_type) == ("83886090", "4242", "kitty", AppType.TERMINAL)
    assert ctx.window_title == title


def test_linux_latin1_title_is_read_as_latin1():
    """A legacy client's STRING-typed WM_NAME is Latin-1, not UTF-8."""
    tools = _x11_tools(b"caf\xe9 - xterm")
    with patch("vox.window.subprocess.run", tools.run):
        ctx = window._detect_active_window_linux(Config())
    assert ctx.window_title == "caf\u00e9 - xterm"
    assert ctx.wm_class == "kitty"


def test_linux_utf8_title_is_read_as_utf8():
    tools = _x11_tools("caf\u00e9 \u2013 na\u00efve".encode())
    with patch("vox.window.subprocess.run", tools.run):
        ctx = window._detect_active_window_linux(Config())
    assert ctx.window_title == "caf\u00e9 \u2013 na\u00efve"


def test_linux_window_without_pid_keeps_its_class():
    tools = _x11_tools(b"Java app", xprop=b'WM_CLASS(STRING) = "sun-awt-X11", "Gnome-terminal"\n_NET_WM_PID:  not found.\n')
    with patch("vox.window.subprocess.run", tools.run):
        ctx = window._detect_active_window_linux(Config())
    assert (ctx.pid, ctx.wm_class, ctx.app_type) == ("", "Gnome-terminal", AppType.TERMINAL)


def test_linux_xprop_failure_keeps_id_and_title():
    tools = _x11_tools(b"Title")
    tools.responses[("xprop", "-id", "83886090")] = subprocess.TimeoutExpired(["xprop"], 2)
    with patch("vox.window.subprocess.run", tools.run):
        ctx = window._detect_active_window_linux(Config())
    assert (ctx.win_id, ctx.window_title, ctx.wm_class, ctx.app_type) == ("83886090", "Title", "", AppType.OTHER)


@pytest.mark.parametrize("error", [
    FileNotFoundError("xdotool"),
    subprocess.TimeoutExpired(["xdotool"], 2),
    subprocess.CalledProcessError(1, ["xdotool"]),
])
def test_linux_detection_failure_returns_unknown_window(error, caplog):
    tools = FakeTools({("xdotool",): error})
    with patch("vox.window.subprocess.run", tools.run):
        ctx = window._detect_active_window_linux(Config())
    assert ctx == AppContext("", "", AppType.OTHER)
    assert any("xdotool" in r.getMessage() for r in caplog.records)


def test_linux_non_numeric_window_id_is_ignored():
    tools = FakeTools({("xdotool", "getactivewindow"): b"1;id\n"})
    with patch("vox.window.subprocess.run", tools.run):
        ctx = window._detect_active_window_linux(Config())
    assert ctx.win_id == ""
    assert tools.calls == [["xdotool", "getactivewindow"]]


# -- Linux screen context ------------------------------------------------------------


def test_linux_ocr_pipes_without_a_shell():
    tools = FakeTools({("maim",): b"PNGDATA", ("tesseract",): b"Kubernetes deployment\n"})
    captured = {}

    def run(args, **kwargs):
        if args[0] == "tesseract":
            captured["input"] = kwargs.get("input")
        return tools.run(args, **kwargs)

    with patch("vox.window.subprocess.run", run):
        assert window._read_ocr("83886090") == "Kubernetes deployment"
    assert tools.calls == [["maim", "-i", "83886090", "--format=png"], ["tesseract", "stdin", "stdout"]]
    assert captured["input"] == b"PNGDATA"


def test_linux_ocr_refuses_a_non_numeric_window_id():
    tools = FakeTools({})
    with patch("vox.window.subprocess.run", tools.run):
        assert window._read_ocr("1;id>/tmp/pwn") == ""
    assert tools.calls == []


def test_atspi_helper_runs_with_safe_path():
    """-P keeps `python -m` from importing a planted vox/ in the daemon's working directory."""
    with patch("vox.window.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="text")) as run:
        assert window._read_atspi_text("4242") == "text"
    argv = run.call_args.args[0]
    assert argv[:4] == [sys.executable, "-P", "-m", "vox._atspi_reader"]
    assert argv[4] == "4242"
    assert run.call_args.kwargs["timeout"] > 0


def test_atspi_helper_needs_a_numeric_pid():
    with patch("vox.window.subprocess.run") as run:
        assert window._read_atspi_text("") == ""
        assert window._read_atspi_text("1 2") == ""
    run.assert_not_called()


# -- tmux ------------------------------------------------------------------------------

# (pid, ppid, tty). Terminal app 500 has one tab: login 800 -> shell 810 -> tmux client 900, and a
# helper 520 with no terminal. Another terminal app, 300, runs tmux client 901 in its own tab.
_PROCESSES = [
    ("500", "1", "none"), ("520", "500", "none"),
    ("800", "500", "tty1"), ("810", "800", "tty1"), ("900", "810", "tty1"),
    ("300", "1", "none"), ("310", "300", "tty5"), ("901", "310", "tty5"),
]
# A second tab of app 500 with a plain shell: login 700 -> shell 710
_SECOND_TAB = [("700", "500", "tty2"), ("710", "700", "tty2")]
# (pid, tty, pane)
_CLIENT_IN_APP = ("900", "tty1", "%1")
_CLIENT_ELSEWHERE = ("901", "tty5", "%2")
_MACOS_TTYS = {"none": "??", "tty1": "ttys001", "tty2": "ttys002", "tty5": "ttys005"}
_LINUX_TTYS = {"none": "?", "tty1": "pts/1", "tty2": "pts/2", "tty5": "pts/5"}


class FakeTmux(FakeTools):
    """ps and tmux list-clients answering in whatever format they are asked for, from one process table."""

    def __init__(self, clients, processes=_PROCESSES, ttys=_MACOS_TTYS) -> None:
        super().__init__({("tmux", "capture-pane"): b"$ kubectl get pods\n"})
        self.clients = clients
        self.processes = processes
        self.ttys = ttys

    def run(self, args, **kwargs):
        if args[:2] == ["tmux", "list-clients"]:
            fields = {"client_pid": 0, "client_tty": 1, "pane_id": 2}
            rows = [(pid, f"/dev/{self.ttys[tty]}", pane) for pid, tty, pane in self.clients]
            self.responses[("tmux", "list-clients")] = "".join(
                re.sub(r"#\{(\w+)\}", lambda m, row=row: row[fields[m.group(1)]], args[-1]) + "\n" for row in rows
            ).encode()
        elif args[:1] == ["ps"]:
            columns = [name.rstrip("=") for flag, spec in itertools.pairwise(args) if flag == "-o" for name in spec.split(",")]
            fields = {"pid": 0, "ppid": 1, "tty": 2}
            rows = [(pid, ppid, self.ttys[tty]) for pid, ppid, tty in self.processes]
            self.responses[("ps",)] = "".join(
                " ".join(f"{row[fields[c]]:>5}" for c in columns) + "\n" for row in rows
            ).encode()
        return super().run(args, **kwargs)


def _read_pane(tools: FakeTools) -> str:
    with patch("vox.window.shutil.which", return_value="/usr/bin/tmux"), patch("vox.window.subprocess.run", tools.run):
        return window._read_tmux_pane("500")


@pytest.mark.parametrize("ttys", [_MACOS_TTYS, _LINUX_TTYS], ids=["macos", "linux"])
def test_tmux_reads_the_pane_of_the_client_inside_the_focused_terminal(ttys):
    tools = FakeTmux([_CLIENT_ELSEWHERE, _CLIENT_IN_APP], ttys=ttys)
    assert _read_pane(tools) == "$ kubectl get pods"
    assert ["tmux", "capture-pane", "-p", "-t", "%1"] in tools.calls


def test_tmux_ignores_sessions_outside_the_focused_terminal():
    """A background tmux session must not stand in for a terminal that is not running tmux."""
    tools = FakeTmux([_CLIENT_ELSEWHERE])
    assert _read_pane(tools) == ""
    assert not tools.called("tmux", "capture-pane")


def test_tmux_is_skipped_when_the_focused_client_is_ambiguous():
    """Two tmux clients in the same terminal app: no way to tell which window is focused."""
    tools = FakeTmux([_CLIENT_IN_APP, ("902", "tty2", "%4")], processes=[*_PROCESSES, *_SECOND_TAB, ("902", "710", "tty2")])
    assert _read_pane(tools) == ""
    assert not tools.called("tmux", "capture-pane")


@pytest.mark.parametrize("ttys", [_MACOS_TTYS, _LINUX_TTYS], ids=["macos", "linux"])
def test_tmux_in_another_tab_of_the_terminal_app_is_not_read(ttys):
    """Every tab shares the app's pid, so the one tmux client may be in a tab the user is not looking at."""
    tools = FakeTmux([_CLIENT_IN_APP], processes=[*_PROCESSES, *_SECOND_TAB], ttys=ttys)
    assert _read_pane(tools) == ""
    assert not tools.called("tmux", "capture-pane")


def test_macos_ocrs_the_focused_window_when_tmux_runs_in_another_tab():
    tools = FakeTmux([_CLIENT_IN_APP], processes=[*_PROCESSES, *_SECOND_TAB])
    with patch("sys.platform", "darwin"), \
         patch("vox.window.shutil.which", return_value="/usr/bin/tmux"), \
         patch("vox.window.subprocess.run", tools.run), \
         patch("vox.window._read_vision_ocr", return_value="focused window text") as ocr:
        assert window._capture_screen_text("77", "500", AppType.TERMINAL) == "focused window text"
    ocr.assert_called_once_with("77")
    assert not tools.called("tmux", "capture-pane")


def test_tmux_needs_the_terminal_pid():
    with patch("vox.window.shutil.which", return_value="/usr/bin/tmux"), patch("vox.window.subprocess.run") as run:
        assert window._read_tmux_pane("") == ""
    run.assert_not_called()


def test_macos_terminal_capture_passes_the_terminal_pid_to_tmux():
    with patch("sys.platform", "darwin"), \
         patch("vox.window._read_tmux_pane", return_value="pane text") as tmux, \
         patch("vox.window._read_vision_ocr") as ocr:
        assert window._capture_screen_text("77", "500", AppType.TERMINAL) == "pane text"
    tmux.assert_called_once_with("500")
    ocr.assert_not_called()


# -- macOS -----------------------------------------------------------------------------


def _fake_objc():
    return SimpleNamespace(autorelease_pool=contextlib.nullcontext)


def _fake_quartz(windows):
    return SimpleNamespace(
        CGWindowListCopyWindowInfo=lambda options, window_id: windows,
        kCGNullWindowID=0, kCGWindowListExcludeDesktopElements=16, kCGWindowListOptionOnScreenOnly=1,
    )


def _fake_ax(title: str | None):
    focused = object()

    def copy_attribute(element, attribute, _):
        if attribute == "AXFocusedWindow":
            return (0, focused) if title is not None else (-25212, None)
        return (0, title) if element is focused else (-25205, None)

    return SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: ("app", pid),
        AXUIElementSetMessagingTimeout=MagicMock(return_value=0),
        AXUIElementCopyAttributeValue=copy_attribute,
        kAXErrorSuccess=0, kAXFocusedWindowAttribute="AXFocusedWindow", kAXTitleAttribute="AXTitle",
    )


def _fake_appkit(name="Ghostty", bundle="com.mitchellh.ghostty", pid=500):
    app = SimpleNamespace(localizedName=lambda: name, bundleIdentifier=lambda: bundle, processIdentifier=lambda: pid)
    workspace = SimpleNamespace(frontmostApplication=lambda: app)
    return SimpleNamespace(NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace))


_WINDOWS = [
    {"kCGWindowOwnerPID": 500, "kCGWindowLayer": 25, "kCGWindowNumber": 10, "kCGWindowName": "menu extra"},
    {"kCGWindowOwnerPID": 600, "kCGWindowLayer": 0, "kCGWindowNumber": 20, "kCGWindowName": "Messages"},
    {"kCGWindowOwnerPID": 500, "kCGWindowLayer": 0, "kCGWindowNumber": 30, "kCGWindowAlpha": 0},
    {"kCGWindowOwnerPID": 500, "kCGWindowLayer": 0, "kCGWindowNumber": 77, "kCGWindowName": "notes.md"},
    {"kCGWindowOwnerPID": 500, "kCGWindowLayer": 0, "kCGWindowNumber": 78, "kCGWindowName": "background"},
]


def test_macos_frontmost_window_is_the_apps_first_normal_window():
    with patch.dict(sys.modules, {"Quartz": _fake_quartz(_WINDOWS)}):
        assert window._frontmost_window(500) == ("77", "notes.md")
        assert window._frontmost_window(999) == ("", "")


def test_macos_detection_records_the_focused_window_id():
    modules = {"objc": _fake_objc(), "AppKit": _fake_appkit(), "Quartz": _fake_quartz(_WINDOWS), "ApplicationServices": _fake_ax(None)}
    with patch.dict(sys.modules, modules), patch("vox.window.subprocess.run") as run:
        ctx = window._detect_active_window_macos(Config())
    assert (ctx.win_id, ctx.pid, ctx.window_title, ctx.app_type) == ("77", "500", "notes.md", AppType.TERMINAL)
    run.assert_not_called()


def test_macos_title_without_screen_recording_comes_from_accessibility():
    """Window names are redacted without Screen Recording; no osascript round trip is spawned."""
    unnamed = [{**w, "kCGWindowName": None} for w in _WINDOWS]
    modules = {"objc": _fake_objc(), "AppKit": _fake_appkit(), "Quartz": _fake_quartz(unnamed), "ApplicationServices": _fake_ax("~/src — zsh")}
    with patch.dict(sys.modules, modules), patch("vox.window.subprocess.run") as run:
        ctx = window._detect_active_window_macos(Config())
    assert (ctx.win_id, ctx.window_title) == ("77", "~/src — zsh")
    run.assert_not_called()


def test_macos_title_falls_back_to_app_name():
    modules = {"objc": _fake_objc(), "AppKit": _fake_appkit(), "Quartz": _fake_quartz([]), "ApplicationServices": _fake_ax(None)}
    with patch.dict(sys.modules, modules), patch("vox.window.subprocess.run") as run:
        ctx = window._detect_active_window_macos(Config())
    assert (ctx.win_id, ctx.window_title) == ("", "Ghostty")
    run.assert_not_called()


def _fake_vision(*lines: str):
    request = MagicMock()
    observations = []
    for text in lines:
        observation = MagicMock()
        observation.topCandidates_.return_value = [MagicMock(string=MagicMock(return_value=text))]
        observations.append(observation)
    request.results.return_value = observations
    handler = MagicMock()
    handler.performRequests_error_.return_value = (True, None)
    vision = MagicMock()
    vision.VNRecognizeTextRequest.alloc.return_value.init.return_value = request
    vision.VNImageRequestHandler.alloc.return_value.initWithURL_options_.return_value = handler
    return vision, request


def test_macos_ocr_captures_only_the_focused_window():
    vision, request = _fake_vision("useState Kubernetes")
    shots = []

    def screencapture(args, **kwargs):
        assert kwargs.get("timeout")
        shots.append(list(args))
        Path(args[-1]).write_bytes(b"PNG")
        return subprocess.CompletedProcess(args, 0)

    modules = {"objc": _fake_objc(), "Vision": vision, "Foundation": MagicMock()}
    with patch.dict(sys.modules, modules), patch("vox.window.subprocess.run", screencapture):
        assert window._read_vision_ocr("77") == "useState Kubernetes"

    (argv,) = shots
    assert argv[:5] == ["screencapture", "-x", "-o", "-l", "77"]
    assert not Path(argv[-1]).exists()
    request.setUsesLanguageCorrection_.assert_called_once_with(False)


def test_macos_ocr_keeps_one_line_per_recognized_line():
    """The secret filter drops a password-like value up to the end of its line, not of the window."""
    vision, _ = _fake_vision("DB_PASSWORD: correct horse battery", "KubeClient handleRequest")

    def screencapture(args, **kwargs):
        Path(args[-1]).write_bytes(b"PNG")
        return subprocess.CompletedProcess(args, 0)

    modules = {"objc": _fake_objc(), "Vision": vision, "Foundation": MagicMock()}
    with patch.dict(sys.modules, modules), patch("vox.window.subprocess.run", screencapture):
        assert window._read_vision_ocr("77") == "DB_PASSWORD: correct horse battery\nKubeClient handleRequest"


def test_macos_ocr_never_falls_back_to_the_whole_screen():
    with patch("vox.window.subprocess.run") as run:
        assert window._read_vision_ocr("") == ""
    run.assert_not_called()


# -- AT-SPI reader -------------------------------------------------------------------------


class Node:
    def __init__(self, text=None, children=(), states=("SHOWING",), pid=None):
        self.text = text
        self.children = list(children)
        self.states = set(states)
        self.pid = pid

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, i):
        return self.children[i]

    def get_state_set(self):
        return SimpleNamespace(contains=lambda state: state in self.states)

    def get_interfaces(self):
        return ["Text"] if self.text is not None else []

    def get_process_id(self):
        return self.pid


def _fake_atspi(desktop=None):
    return SimpleNamespace(
        StateType=SimpleNamespace(ACTIVE="ACTIVE", SHOWING="SHOWING"),
        Text=SimpleNamespace(get_character_count=lambda n: len(n.text), get_text=lambda n, start, end: n.text[start:end]),
        init=lambda: None,
        get_desktop=lambda i: desktop,
    )


def test_atspi_reads_only_the_active_window_and_what_is_showing(capsys, monkeypatch):
    background = Node(children=[Node("other window secret")], states=("SHOWING",))
    focused = Node(
        children=[Node("visible text"), Node("hidden tab secret", states=()), Node(children=[Node("nested visible")])],
        states=("SHOWING", "ACTIVE"),
    )
    other_app = Node(children=[Node(children=[Node("another app")], states=("ACTIVE", "SHOWING"))], pid=1)
    app = Node(children=[background, focused], pid=4242)
    atspi = _fake_atspi(Node(children=[other_app, app]))
    gi = SimpleNamespace(require_version=lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["_atspi_reader", "4242", "2000"])

    with patch.dict(sys.modules, {"gi": gi, "gi.repository": SimpleNamespace(Atspi=atspi)}):
        _atspi_reader.main()

    assert capsys.readouterr().out == "visible text\nnested visible"


def test_atspi_prints_nothing_without_an_active_window(capsys, monkeypatch):
    app = Node(children=[Node(children=[Node("text")])], pid=4242)
    atspi = _fake_atspi(Node(children=[app]))
    monkeypatch.setattr(sys, "argv", ["_atspi_reader", "4242"])

    with patch.dict(sys.modules, {"gi": SimpleNamespace(require_version=lambda *a: None), "gi.repository": SimpleNamespace(Atspi=atspi)}):
        _atspi_reader.main()

    assert capsys.readouterr().out == ""


# -- Classification ------------------------------------------------------------------------


@pytest.mark.parametrize(("identifier", "expected"), [
    ("ru.keepcoder.telegram Telegram", AppType.CHAT),
    ("TelegramDesktop", AppType.CHAT),
    ("com.microsoft.VSCode Code", AppType.EDITOR),
    ("com.apple.Terminal Terminal", AppType.TERMINAL),
    ("com.googlecode.iterm2 iTerm2", AppType.TERMINAL),
    ("Gnome-terminal", AppType.TERMINAL),
    ("Terminator", AppType.TERMINAL),
    ("org.gnome.Console", AppType.TERMINAL),
    ("kgx", AppType.TERMINAL),
    ("org.gnome.Ptyxis", AppType.TERMINAL),
    ("URxvt", AppType.TERMINAL),
    ("st-256color", AppType.TERMINAL),
    ("Gimp", AppType.OTHER),
])
def test_classify(identifier, expected):
    assert _classify(identifier, "", Config()) == expected


@pytest.mark.parametrize(("pattern", "app_type"), sorted(window._DEFAULT_CLASSES.items(), key=lambda item: item[0]))
def test_every_default_pattern_is_reachable(pattern, app_type):
    """No default pattern is shadowed by a shorter one inside it."""
    assert _classify(pattern, "", Config()) == app_type


def test_malformed_user_window_class_is_ignored(caplog):
    """A wrong type in [window_classes] is warned about, never raised out of detection."""
    config = Config(window_classes={"kitty": 3})
    assert _classify("kitty", "", config) == AppType.TERMINAL
    assert any("Invalid app type" in r.getMessage() for r in caplog.records)


def test_user_window_classes_take_precedence():
    config = Config(window_classes={"telegram": "email"})
    assert _classify("ru.keepcoder.telegram Telegram", "", config) == AppType.EMAIL


def test_vim_title_is_an_editor():
    assert _classify("Unknown", "notes.md", Config()) == AppType.OTHER
    assert _classify("Unknown", "nvim notes.md", Config()) == AppType.EDITOR
    assert _classify("Unknown", "notes.md - VIM", Config()) == AppType.EDITOR


@pytest.mark.parametrize('acquired,raises', [(True, False), (True, True), (False, False)])
def test_overlay_guard_brackets_only_screenshot_and_restores(acquired, raises):
    from contextlib import contextmanager

    from vox.window import _read_vision_ocr

    events = []
    @contextmanager
    def guard():
        events.append('hidden')
        try:
            yield acquired
        finally:
            events.append('restored')

    def shot(*args, **kwargs):
        assert events == ['hidden']
        events.append('screenshot')
        if raises:
            raise OSError('capture failed')
        return type('Result', (), {'returncode': 1})()

    with patch('vox.window.subprocess.run', side_effect=shot) as capture, patch.dict(sys.modules, {'objc': MagicMock()}):
        assert _read_vision_ocr('123', guard) == ''
    assert events == (['hidden', 'screenshot', 'restored'] if acquired else ['hidden', 'restored'])
    assert capture.call_count == int(acquired)


@pytest.mark.parametrize('acquired,raises', [(True, False), (True, True), (False, False)])
def test_linux_capture_guard_restores_before_ocr(acquired, raises):
    from contextlib import contextmanager

    events = []
    @contextmanager
    def guard():
        events.append('hidden')
        try:
            yield acquired
        finally:
            events.append('restored')

    def run(args, **kwargs):
        if args[0] == 'maim':
            assert events == ['hidden']
            events.append('screenshot')
            if raises:
                raise OSError('capture failed')
            return SimpleNamespace(stdout=b'PNG')
        assert events == ['hidden', 'screenshot', 'restored']
        events.append('ocr')
        return SimpleNamespace(stdout=b'target text')

    with patch('vox.window.subprocess.run', side_effect=run):
        assert window._read_ocr('123', guard) == ('target text' if acquired and not raises else '')
    assert events[-1] == ('ocr' if acquired and not raises else 'restored')
