"""Main-thread AppKit recording panel. Imported only by the supported GUI backend."""
from __future__ import annotations

import io
import threading

import AppKit
import objc
from Foundation import NSData, NSNotificationCenter, NSOperationQueue, NSRunLoop, NSRunLoopCommonModes, NSTimer

from ..icons import IconState, make_app_icon
from ..overlay import (
    CANCEL_RECT,
    PANEL_SIZE,
    SIGNAL_HEIGHT,
    SIGNAL_WIDTH,
    SIGNAL_X,
    SIGNAL_Y,
    bead_circles,
    panel_frame,
    quartz_to_appkit,
    wave_paths,
)


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('The recording panel requires the main thread')


def _color(hex_value, alpha=1):
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        *[int(hex_value[i:i + 2], 16) / 255 for i in (0, 2, 4)], alpha,
    )


def _rect(rect):
    return rect.origin.x, rect.origin.y, rect.size.width, rect.size.height


def _nsrect(rect):
    x, y, w, h = rect
    return AppKit.NSMakeRect(x, y, w, h)


def _screens():
    return [(int(s.deviceDescription()['NSScreenNumber']), _rect(s.frame()), _rect(s.visibleFrame()))
            for s in AppKit.NSScreen.screens()]


def _target_bounds(context, primary_height):
    from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListOptionOnScreenOnly

    windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or ()
    target = next((w for w in windows if str(w.get('kCGWindowNumber')) == context.win_id), None)
    if target is None:
        target = next((w for w in windows if str(w.get('kCGWindowOwnerPID')) == context.pid
                       and w.get('kCGWindowLayer') == 0), None)
    bounds = target.get('kCGWindowBounds') if target else None
    if bounds:
        return quartz_to_appkit(tuple(float(bounds[k]) for k in ('X', 'Y', 'Width', 'Height')), primary_height)
    return None


class RecordingPanel(AppKit.NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class CancelButton(AppKit.NSButton):
    def acceptsFirstMouse_(self, event):
        return True

    def mouseDown_(self, event):
        self.generation = self.owner.snapshot.generation
        objc.super(CancelButton, self).mouseDown_(event)

    def cancel_(self, sender):
        generation, self.generation = self.generation, None
        self.owner.on_cancel(self.owner.snapshot.generation if generation is None else generation)


class SignalView(AppKit.NSView):
    def drawRect_(self, rect):
        if self.owner is None:
            return
        try:
            self.paint()
        except Exception:
            self.owner.on_error()

    @objc.python_method
    def paint(self):
        _main_thread()
        width, height = self.bounds().size
        rim = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSInsetRect(self.bounds(), 0.5, 0.5), 12, 12)
        _color('1b1d20', 0.95).setFill()
        rim.fill()
        _color('51504b', 0.65).setStroke()
        rim.setLineWidth_(0.75)
        rim.stroke()
        scale = min(1, width / PANEL_SIZE[0], height / PANEL_SIZE[1])
        transform = AppKit.NSAffineTransform.transform()
        transform.scaleBy_(scale)
        AppKit.NSGraphicsContext.saveGraphicsState()
        try:
            transform.concat()
            cross = AppKit.NSBezierPath.bezierPath()
            cx, cy = CANCEL_RECT[0] + 9, PANEL_SIZE[1] - CANCEL_RECT[1] - 9
            for dy in (-3, 3):
                cross.moveToPoint_((cx - 3, cy + dy))
                cross.lineToPoint_((cx + 3, cy - dy))
            _color('bfc3ca').setStroke()
            cross.setLineWidth_(1.25)
            cross.stroke()
            icon_state = IconState.RECORDING if self.owner.snapshot.phase == 'listening' else IconState.PROCESSING
            self.owner.icons[icon_state].drawInRect_fromRect_operation_fraction_(
                AppKit.NSMakeRect(6, (PANEL_SIZE[1] - 40) / 2, 40, 40), AppKit.NSZeroRect, AppKit.NSCompositingOperationSourceOver, 1)
            local = self.owner.snapshot.mode == 'whisper_cpp'
            colors = ['d6dce5', '9ba5b5', '606979'] if local else ['c9a24a', '8f7436', '5c4c30']
            attrs = {
                AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(8),
                AppKit.NSForegroundColorAttributeName: _color('d6dce5' if local else 'c2c3c5'),
            }
            label = AppKit.NSString.stringWithString_(self.owner.snapshot.status)
            label_width = label.sizeWithAttributes_(attrs).width
            label.drawAtPoint_withAttributes_((SIGNAL_X + (SIGNAL_WIDTH - label_width) / 2, 3), attrs)
            x0, wave_width, middle = SIGNAL_X, SIGNAL_WIDTH, PANEL_SIZE[1] - SIGNAL_Y
            if self.owner.snapshot.phase != 'listening':
                track = AppKit.NSBezierPath.bezierPath()
                track.moveToPoint_((x0, middle))
                track.lineToPoint_((x0 + wave_width, middle))
                _color(colors[0], 0.4).setStroke()
                track.setLineWidth_(1)
                track.stroke()
                for x, radius, alpha in bead_circles(self.owner.phase, width=wave_width):
                    _color(('f5f7ff' if local else 'ffe6a6') if radius < 2 else colors[0], alpha).setFill()
                    AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                        AppKit.NSMakeRect(x0 + x - radius, middle - radius, radius * 2, radius * 2)).fill()
            else:
                ruler = AppKit.NSBezierPath.bezierPath()
                ruler.moveToPoint_((x0, middle))
                ruler.lineToPoint_((x0 + wave_width, middle))
                for x in range(0, wave_width + 1, 24):
                    ruler.moveToPoint_((x0 + x, middle - 2))
                    ruler.lineToPoint_((x0 + x, middle + 2))
                _color('383735').setStroke()
                ruler.setLineWidth_(1)
                ruler.stroke()
                amplitude, t = self.owner.amplitude, self.owner.phase
                for layer, points in wave_paths(amplitude, t, width=wave_width, height=SIGNAL_HEIGHT):
                    path = AppKit.NSBezierPath.bezierPath()
                    for x, y in points:
                        (path.moveToPoint_ if x == 0 else path.lineToPoint_)((x0 + x, middle + y))
                    _color(colors[layer]).setStroke()
                    path.setLineWidth_(1.7 if layer == 0 else 1)
                    path.stroke()
        finally:
            AppKit.NSGraphicsContext.restoreGraphicsState()


class NativeOverlay:
    def __init__(self):
        _main_thread()
        self.panel = None
        self.cancel_panel = None
        self.on_cancel = lambda generation: None
        self._timer = None
        self._observers = []
        self._closed = False
        self.snapshot = None
        self.screen_id = None
        self.amplitude = self.phase = 0.0
        self.on_error = lambda: None
        try:
            self.panel = RecordingPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                AppKit.NSMakeRect(0, 0, *PANEL_SIZE),
                AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
                AppKit.NSBackingStoreBuffered, False)
            self.panel.setReleasedWhenClosed_(False)
            self.panel.setOpaque_(False)
            self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
            self.panel.setHasShadow_(True)
            self.panel.setIgnoresMouseEvents_(True)
            self.panel.setHidesOnDeactivate_(False)
            self.panel.setFloatingPanel_(True)
            self.panel.setLevel_(AppKit.NSScreenSaverWindowLevel)
            behavior = (AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
                        AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary |
                        AppKit.NSWindowCollectionBehaviorStationary |
                        AppKit.NSWindowCollectionBehaviorIgnoresCycle)
            behavior |= getattr(AppKit, 'NSWindowCollectionBehaviorCanJoinAllApplications', 0)
            self.panel.setCollectionBehavior_(behavior)
            self.panel.setSharingType_(AppKit.NSWindowSharingNone)
            self.panel.setAnimationBehavior_(AppKit.NSWindowAnimationBehaviorNone)
            self.view = SignalView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, *PANEL_SIZE))
            self.view.owner = self
            self.panel.setContentView_(self.view)
            self.cancel_panel = RecordingPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                AppKit.NSMakeRect(0, 0, 18, 18),
                self.panel.styleMask(), AppKit.NSBackingStoreBuffered, False)
            self.cancel_panel.setReleasedWhenClosed_(False)
            self.cancel_panel.setOpaque_(False)
            # WindowServer passes clicks through entirely transparent windows.
            # A barely visible backing makes the whole button target clickable.
            self.cancel_panel.setBackgroundColor_(_color("1b1d20", 0.01))
            self.cancel_panel.setHasShadow_(False)
            self.cancel_panel.setHidesOnDeactivate_(False)
            self.cancel_panel.setLevel_(self.panel.level())
            self.cancel_panel.setCollectionBehavior_(behavior)
            self.cancel_panel.setSharingType_(AppKit.NSWindowSharingNone)
            self.cancel_panel.setAnimationBehavior_(AppKit.NSWindowAnimationBehaviorNone)
            self.cancel_button = CancelButton.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 18, 18))
            self.cancel_button.owner = self
            self.cancel_button.generation = None
            self.cancel_button.setTitle_('')
            self.cancel_button.setBordered_(False)
            self.cancel_button.setFocusRingType_(AppKit.NSFocusRingTypeNone)
            self.cancel_button.setTarget_(self.cancel_button)
            self.cancel_button.setAction_('cancel:')
            self.cancel_button.setAccessibilityLabel_('Cancel recording or transcription')
            self.cancel_button.setToolTip_('Cancel')
            self.cancel_panel.setContentView_(self.cancel_button)
            self.icons = {}
            for state in (IconState.RECORDING, IconState.PROCESSING):
                png = io.BytesIO()
                make_app_icon(128, with_plate=False, state=state).save(png, format='PNG')
                data = png.getvalue()
                self.icons[state] = AppKit.NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
            center = NSNotificationCenter.defaultCenter()
            self._observe(center, AppKit.NSApplicationDidChangeScreenParametersNotification, self._display_changed)
        except Exception:
            self.close()
            raise

    @property
    def reduced_motion(self):
        _main_thread()
        return bool(AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())

    def _observe(self, center, name, callback):
        def notified(_notification):
            if self._closed:
                return
            try:
                callback()
            except Exception:
                self.on_error()
        token = center.addObserverForName_object_queue_usingBlock_(name, None, NSOperationQueue.mainQueue(), notified)
        self._observers.append((center, token))

    def observe_motion(self, callback):
        _main_thread()
        self._observe(AppKit.NSWorkspace.sharedWorkspace().notificationCenter(),
                      AppKit.NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification,
                      lambda: callback(self.reduced_motion))

    def _place(self, new=False):
        screens = _screens()
        if not screens:
            raise RuntimeError('No display available')
        target = None
        if new:
            try:
                target = _target_bounds(self.snapshot.context, screens[0][1][3])
            except Exception:
                pass  # optional geometry; the primary screen is a usable fallback
        else:
            screens = [s for s in screens if s[0] == self.screen_id] or screens[:1]
        self.screen_id, frame = panel_frame(screens, target)
        self.panel.setFrame_display_(_nsrect(frame), True)
        x, y, width, height = frame
        scale = min(1, width / PANEL_SIZE[0], height / PANEL_SIZE[1])
        cx, cy, cw, ch = CANCEL_RECT
        self.cancel_panel.setFrame_display_(
            _nsrect((x + cx * scale, y + (PANEL_SIZE[1] - cy - ch) * scale, cw * scale, ch * scale)), True)

    def _display_changed(self):
        _main_thread()
        if self.panel.isVisible():
            self._place()

    def show(self, snapshot):
        _main_thread()
        new = self.snapshot is None or self.snapshot.generation != snapshot.generation
        self.snapshot = snapshot
        self._place(new)
        self.panel.setAlphaValue_(1)
        self.panel.orderFrontRegardless()
        if snapshot.phase in ("listening", "processing"):
            self.cancel_panel.orderFrontRegardless()
        else:
            self.cancel_panel.orderOut_(None)

    def draw(self, amplitude, phase, opacity):
        _main_thread()
        self.amplitude, self.phase = amplitude, phase
        self.panel.setAlphaValue_(opacity)
        self.view.setNeedsDisplay_(True)

    def timer(self, callback):
        _main_thread()
        if self._timer is not None:
            timer, self._timer = self._timer, None
            timer.invalidate()
        if callback is not None:
            self._timer = NSTimer.timerWithTimeInterval_repeats_block_(1 / 30, True, lambda _: callback())
            NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

    def hide(self):
        _main_thread()
        if self.cancel_panel is not None:
            self.cancel_panel.orderOut_(None)
        if self.panel is not None:
            self.panel.orderOut_(None)

    def request_close(self):
        """Thread-safe emergency teardown, independent of the tray's dispatcher."""
        def close():
            try:
                self.close()
            except Exception:
                pass  # the controller already reported the optional backend failure
        NSOperationQueue.mainQueue().addOperationWithBlock_(close)

    def close(self):
        _main_thread()
        # Attempt every cleanup even if one native operation fails.
        self._closed = True
        errors = []
        try:
            self.timer(None)
        except Exception as exc:
            errors.append(exc)
        observers, self._observers = self._observers, []
        for center, token in observers:
            try:
                center.removeObserver_(token)
            except Exception as exc:
                errors.append(exc)
        cancel_panel, self.cancel_panel = getattr(self, 'cancel_panel', None), None
        if cancel_panel is not None:
            for action in (lambda: cancel_panel.orderOut_(None), cancel_panel.close):
                try:
                    action()
                except Exception as exc:
                    errors.append(exc)
            if hasattr(self, "cancel_button"):
                self.cancel_button.owner = None
        self.on_cancel = lambda generation: None
        panel, self.panel = self.panel, None
        if panel is not None:
            for action in (lambda: panel.orderOut_(None), panel.close):
                try:
                    action()
                except Exception as exc:
                    errors.append(exc)
        if hasattr(self, 'view'):
            self.view.owner = None
        self.on_error = lambda: None
        if errors:
            raise errors[0]
