"""Real GTK 3/X11 backend tests, in the tray's process (separate from GTK 4 Settings)."""
import os
import subprocess
import sys
import threading
from unittest.mock import Mock, patch

import pytest

from vox.ui.overlay import PANEL_SIZE, Snapshot
from vox.window import AppContext, AppType


@pytest.fixture
def x11():
    if not sys.platform.startswith('linux') or not os.environ.get('DISPLAY'):
        pytest.skip('Requires a Linux X11 display')
    from vox.ui.x11 import overlay
    overlay.Gtk.init_check()
    if not isinstance(overlay.Gdk.Display.get_default(), overlay.GdkX11.X11Display):
        pytest.skip('Not an X11 display')
    yield overlay
    context = overlay.GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def test_native_flags_target_monitor_draw_and_cleanup(x11, tmp_path):
    native = x11.NativeOverlay()
    target = x11.Gtk.Window()
    target.show_all()
    motion = Mock()
    errors = []
    native.on_error = lambda: errors.append(True)
    try:
        native.observe_motion(motion)
        assert not native.panel.get_accept_focus()
        assert not native.panel.get_focus_on_map()
        assert not native.panel.get_decorated()
        assert native.panel.get_skip_taskbar_hint() and native.panel.get_skip_pager_hint()
        context = AppContext('', '', AppType.EDITOR, win_id=str(target.get_window().get_xid()))
        for phase, mode in [('listening', 'batch'), ('processing', 'batch'), ('processing', 'whisper_cpp')]:
            native.show(Snapshot(1, 1, phase, mode, context))
            native.draw(0.7, 1, 1)
            surface = x11.cairo.ImageSurface(x11.cairo.FORMAT_ARGB32, *PANEL_SIZE)
            native._paint(x11.cairo.Context(surface), *PANEL_SIZE)
            assert any(surface.get_data())
            assert native.panel.get_visible()
            assert native.monitor == native.display.get_monitor_at_window(target.get_window())
        assert not errors
        settings = native.settings
        original = settings.get_property('gtk-enable-animations')
        settings.set_property('gtk-enable-animations', not original)
        motion.assert_called_with(original)
        settings.set_property('gtk-enable-animations', original)
        native.timer(lambda: None)
        assert native._timer is not None
        native.hide()
        assert not native.panel.get_visible()
        native.close()
        assert native.panel is None and native._timer is None
        assert not native._signals and not native._monitor_signals
        calls = motion.call_count
        settings.set_property('gtk-enable-animations', not original)
        settings.set_property('gtk-enable-animations', original)
        assert motion.call_count == calls
    finally:
        native.close()
        target.destroy()


def test_missing_window_falls_back_and_monitor_change_preserves_selection(x11):
    native = x11.NativeOverlay()
    try:
        native.show(Snapshot(1, 1, 'listening', 'batch', AppContext('', '', AppType.OTHER, win_id='99999999')))
        assert native.monitor in native._monitors()
        selected = native.monitor
        native._display_changed()
        assert native.monitor == selected
        context = x11.GLib.MainContext.default()
        for _ in range(100):
            context.iteration(False)
        area = selected.get_workarea()
        x, y = native.panel.get_position()
        width, height = native.panel.get_size()
        assert x >= area.x and y >= area.y
        assert x + width <= area.x + area.width and y + height <= area.y + area.height
    finally:
        native.close()


def test_backend_rejects_background_creation(x11):
    errors = []
    def build():
        try:
            x11.NativeOverlay()
        except RuntimeError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=build)
    thread.start()
    thread.join()
    assert errors == ['The recording panel requires the main thread']


def test_linux_factory_is_lazy_and_uses_native_backend(x11):
    from vox.ui.overlay import create_overlay
    overlay = create_overlay(True, lambda fn: fn())
    assert overlay.backend is None
    recorder = Mock(latest_level=None)
    try:
        overlay.begin(1)
        overlay.listening(1, AppContext('', '', AppType.OTHER), 'batch', recorder)
        assert isinstance(overlay.backend, x11.NativeOverlay)
        overlay.processing(1)
        assert overlay.backend.snapshot.status == 'Transcribing…'
    finally:
        overlay.close()
    assert overlay.backend is None


def test_cairo_is_available_in_a_clean_installed_style_venv(x11, tmp_path):
    """Installer/.deb link both distro bindings; the Cairo bridge must work without system-site-packages."""
    import pathlib

    import cairo
    import gi

    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(tmp_path / 'venv')], check=True)
    python = tmp_path / 'venv/bin/python'
    purelib = subprocess.check_output([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
    for module in (gi, cairo):
        (pathlib.Path(purelib) / module.__name__).symlink_to(pathlib.Path(module.__file__).parent)
    subprocess.run([str(python), '-c', 'import gi, cairo; gi.require_foreign("cairo"); gi.require_version("Gtk", "3.0"); from gi.repository import Gtk'], check=True)


def test_hide_synchronizes_x_before_capture_ack(x11):
    native = x11.NativeOverlay()
    try:
        with patch.object(native, 'display') as display:
            native.hide()
            display.sync.assert_called_once()
    finally:
        native.close()
