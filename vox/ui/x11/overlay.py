"""Non-activating X11 recording panel on the tray's GTK 3/GLib loop."""
from __future__ import annotations

import io
import math
import threading

import cairo
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkX11', '3.0')
gi.require_foreign('cairo')
from gi.repository import Gdk, GdkPixbuf, GdkX11, GLib, Gtk  # noqa: E402

from ..icons import IconState, make_app_icon  # noqa: E402
from ..overlay import PANEL_SIZE, SIGNAL_HEIGHT, SIGNAL_WIDTH, SIGNAL_X, SIGNAL_Y, bead_circles, panel_frame, wave_paths  # noqa: E402


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('The recording panel requires the main thread')


def _rect(rect):
    return rect.x, rect.y, rect.width, rect.height


def _color(ctx, value, alpha=1):
    ctx.set_source_rgba(*[int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)], alpha)


def _rounded(ctx, x, y, width, height, radius=12):
    for cx, cy, start in ((x + width - radius, y + radius, -math.pi / 2),
                          (x + width - radius, y + height - radius, 0),
                          (x + radius, y + height - radius, math.pi / 2),
                          (x + radius, y + radius, math.pi)):
        ctx.arc(cx, cy, radius, start, start + math.pi / 2)
    ctx.close_path()


class NativeOverlay:
    def __init__(self):
        _main_thread()
        self.panel = None
        self._timer = None
        self._signals = []
        self._monitor_signals = []
        self._closed = False
        self.on_error = lambda: None
        self.snapshot = None
        self.monitor = None
        self.amplitude = self.phase = 0.0
        self.display = Gdk.Display.get_default()
        if not isinstance(self.display, GdkX11.X11Display):
            raise RuntimeError('The recording overlay requires an X11 display')
        try:
            self.panel = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            self.panel.set_title('Vox recording overlay')
            self.panel.set_decorated(False)
            self.panel.set_resizable(False)
            self.panel.set_accept_focus(False)
            self.panel.set_focus_on_map(False)
            self.panel.set_skip_taskbar_hint(True)
            self.panel.set_skip_pager_hint(True)
            self.panel.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            self.panel.set_keep_above(True)
            self.panel.stick()
            self.panel.set_app_paintable(True)
            visual = self.panel.get_screen().get_rgba_visual()
            if visual is not None:
                self.panel.set_visual(visual)
            self.panel.set_default_size(*PANEL_SIZE)
            self.panel.connect('draw', self._draw)
            self.panel.realize()
            # An empty input region passes every pointer event to the window underneath.
            self.panel.get_window().input_shape_combine_region(cairo.Region(), 0, 0)
            self.icons = {}
            for state in (IconState.RECORDING, IconState.PROCESSING):
                png = io.BytesIO()
                make_app_icon(128, with_plate=False, state=state).save(png, format='PNG')
                loader = GdkPixbuf.PixbufLoader.new_with_type('png')
                loader.write(png.getvalue())
                loader.close()
                self.icons[state] = loader.get_pixbuf()
            self.settings = Gtk.Settings.get_default()
            for signal in ('monitor-added', 'monitor-removed'):
                self._signals.append((self.display, self.display.connect(signal, self._display_changed)))
            self._watch_monitors()
        except Exception:
            self.close()
            raise

    @property
    def reduced_motion(self):
        _main_thread()
        return not self.settings.get_property('gtk-enable-animations')

    def observe_motion(self, callback):
        _main_thread()
        def changed(*_):
            if not self._closed:
                try:
                    callback(self.reduced_motion)
                except Exception:
                    self.on_error()
        self._signals.append((self.settings, self.settings.connect('notify::gtk-enable-animations', changed)))

    def _monitors(self):
        monitors = [self.display.get_monitor(i) for i in range(self.display.get_n_monitors())]
        primary = self.display.get_primary_monitor()
        return sorted(monitors, key=lambda monitor: monitor != primary)

    def _watch_monitors(self):
        for obj, signal in self._monitor_signals:
            obj.disconnect(signal)
        self._monitor_signals.clear()
        for monitor in self._monitors():
            for name in ('workarea', 'geometry', 'scale-factor'):
                self._monitor_signals.append((monitor, monitor.connect(f'notify::{name}', self._display_changed)))

    def _target_monitor(self):
        context = self.snapshot.context
        if context is None:
            return None
        monitor = self._monitor_for_window(context.win_id)
        if monitor is not None:
            return monitor
        # If the captured window disappeared, use another normal window of the same app.
        if context.pid.isdecimal():
            from ...window import _run_tool
            result = _run_tool(['xdotool', 'search', '--onlyvisible', '--pid', context.pid], timeout=0.25) or ''
            for win_id in result.splitlines():
                monitor = self._monitor_for_window(win_id)
                if monitor is not None:
                    return monitor
        return None

    def _monitor_for_window(self, win_id):
        if not win_id.isdecimal():
            return None
        self.display.error_trap_push()
        try:
            window = GdkX11.X11Window.foreign_new_for_display(self.display, int(win_id))
            monitor = self.display.get_monitor_at_window(window) if window is not None else None
            self.display.sync()
        except (TypeError, OverflowError):
            monitor = None  # the captured window was destroyed before placement
        finally:
            error = self.display.error_trap_pop()
        return monitor if not error else None

    def _place(self, new=False):
        monitors = self._monitors()
        if not monitors:
            raise RuntimeError('No display available')
        if new:
            self.monitor = self._target_monitor()
        if self.monitor not in monitors:
            self.monitor = monitors[0]
        screen = (self.monitor, _rect(self.monitor.get_geometry()), _rect(self.monitor.get_workarea()))
        _, (x, y, width, height) = panel_frame([screen], top_left=True)
        self.panel.move(round(x), round(y))
        self.panel.set_size_request(round(width), round(height))
        self.panel.resize(round(width), round(height))
        # A rounded bounding shape keeps the corners clean even without a compositor.
        region = cairo.Region()
        radius = min(12, height / 2, width / 2)
        for row in range(round(height)):
            dy = max(radius - row - 0.5, row + 0.5 - (height - radius), 0)
            inset = math.ceil(radius - math.sqrt(max(0, radius * radius - dy * dy))) if dy else 0
            region.union(cairo.RectangleInt(inset, row, max(1, round(width) - 2 * inset), 1))
        self.panel.get_window().shape_combine_region(region, 0, 0)
        self.panel.get_window().input_shape_combine_region(cairo.Region(), 0, 0)

    def _display_changed(self, *_):
        if self._closed:
            return
        try:
            self._watch_monitors()
            if self.panel.get_visible():
                self._place()
        except Exception:
            self.on_error()

    def show(self, snapshot):
        _main_thread()
        new = self.snapshot is None or self.snapshot.generation != snapshot.generation
        self.snapshot = snapshot
        self._place(new)
        self.panel.set_opacity(1)
        self.panel.show_all()  # never present(): no request to activate or focus
        self.panel.get_window().raise_()

    def draw(self, amplitude, phase, opacity):
        _main_thread()
        self.amplitude, self.phase = amplitude, phase
        self.panel.set_opacity(opacity)
        self.panel.queue_draw()

    def _draw(self, widget, ctx):
        if self._closed or self.snapshot is None:
            return False
        try:
            self._paint(ctx, widget.get_allocated_width(), widget.get_allocated_height())
        except Exception:
            self.on_error()
        return True

    def _paint(self, ctx, width, height):
        _main_thread()
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.set_source_rgba(0, 0, 0, 0)
        ctx.paint()
        ctx.set_operator(cairo.OPERATOR_OVER)
        _rounded(ctx, 0.5, 0.5, width - 1, height - 1, min(12, (height - 1) / 2, (width - 1) / 2))
        _color(ctx, '1b1d20', 0.95 if self.panel.get_screen().is_composited() else 1)
        ctx.fill_preserve()
        _color(ctx, '51504b', 0.65)
        ctx.set_line_width(0.75)
        ctx.stroke()
        ctx.save()
        try:
            scale = min(1, width / PANEL_SIZE[0], height / PANEL_SIZE[1])
            ctx.scale(scale, scale)
            ctx.save()
            ctx.translate(6, (PANEL_SIZE[1] - 40) / 2)
            icon_state = IconState.RECORDING if self.snapshot.phase == 'listening' else IconState.PROCESSING
            icon = self.icons[icon_state]
            ctx.scale(40 / icon.get_width(), 40 / icon.get_height())
            Gdk.cairo_set_source_pixbuf(ctx, icon, 0, 0)
            ctx.paint()
            ctx.restore()
            local = self.snapshot.mode == 'whisper_cpp'
            colors = ['d6dce5', '9ba5b5', '606979'] if local else ['c9a24a', '8f7436', '5c4c30']
            _color(ctx, 'd6dce5' if local else 'c2c3c5')
            ctx.select_font_face('sans-serif')
            ctx.set_font_size(8)
            extent = ctx.text_extents(self.snapshot.status)
            ctx.move_to(SIGNAL_X + (SIGNAL_WIDTH - extent.width) / 2 - extent.x_bearing, PANEL_SIZE[1] - 6)
            ctx.show_text(self.snapshot.status)
            x0, wave_width, middle = SIGNAL_X, SIGNAL_WIDTH, SIGNAL_Y
            if self.snapshot.phase != 'listening':
                _color(ctx, colors[0], 0.4)
                ctx.set_line_width(1)
                ctx.move_to(x0, middle)
                ctx.line_to(x0 + wave_width, middle)
                ctx.stroke()
                for x, radius, alpha in bead_circles(self.phase, width=wave_width):
                    _color(ctx, ('f5f7ff' if local else 'ffe6a6') if radius < 2 else colors[0], alpha)
                    ctx.arc(x0 + x, middle, radius, 0, 2 * math.pi)
                    ctx.fill()
            else:
                _color(ctx, '383735')
                ctx.set_line_width(1)
                ctx.move_to(x0, middle)
                ctx.line_to(x0 + wave_width, middle)
                for x in range(0, wave_width + 1, 24):
                    ctx.move_to(x0 + x, middle - 2)
                    ctx.line_to(x0 + x, middle + 2)
                ctx.stroke()
                for layer, points in wave_paths(self.amplitude, self.phase, width=wave_width, height=SIGNAL_HEIGHT):
                    for x, y in points:
                        (ctx.move_to if x == 0 else ctx.line_to)(x0 + x, middle - y)
                    _color(ctx, colors[layer])
                    ctx.set_line_width(1.7 if layer == 0 else 1)
                    ctx.stroke()
        finally:
            ctx.restore()

    def timer(self, callback):
        _main_thread()
        if self._timer is not None:
            source, self._timer = self._timer, None
            GLib.source_remove(source)
        if callback is not None:
            def tick():
                callback()
                return GLib.SOURCE_CONTINUE if self._timer is not None else GLib.SOURCE_REMOVE
            self._timer = GLib.timeout_add(33, tick)

    def hide(self):
        _main_thread()
        if self.panel is not None:
            self.panel.hide()
            self.display.sync()  # server has processed UnmapWindow before capture receives its ack

    def request_close(self):
        def close():
            try:
                self.close()
            except Exception:
                pass  # controller has already reported the optional backend failure
            return GLib.SOURCE_REMOVE
        GLib.idle_add(close)

    def close(self):
        _main_thread()
        self._closed = True
        errors = []
        try:
            self.timer(None)
        except Exception as exc:
            errors.append(exc)
        signals, self._signals = self._signals + self._monitor_signals, []
        self._monitor_signals = []
        for obj, signal in signals:
            try:
                obj.disconnect(signal)
            except Exception as exc:
                errors.append(exc)
        panel, self.panel = self.panel, None
        if panel is not None:
            for action in (panel.hide, self.display.sync, panel.destroy):
                try:
                    action()
                except Exception as exc:
                    errors.append(exc)
        self.on_error = lambda: None
        if errors:
            raise errors[0]
