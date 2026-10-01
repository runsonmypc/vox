"""Actual AppKit construction/drawing checks; no focus or clipboard changes."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vox.ui.overlay import Snapshot


def test_native_panel_flags_drawing_motion_and_teardown(appkit, tmp_path):
    from vox.ui.mac.overlay import NativeOverlay
    from vox.window import AppContext, AppType

    native = NativeOverlay()
    errors = []
    native.on_error = lambda: errors.append(True)
    try:
        panel = native.panel
        assert not panel.canBecomeKeyWindow() and not panel.canBecomeMainWindow()
        assert panel.ignoresMouseEvents() and panel.level() > 0
        assert panel.styleMask() & appkit.NSWindowStyleMaskNonactivatingPanel
        assert panel.collectionBehavior() & appkit.NSWindowCollectionBehaviorFullScreenAuxiliary
        assert panel.sharingType() == appkit.NSWindowSharingNone
        assert not panel.hidesOnDeactivate()
        motion = Mock()
        native.observe_motion(motion)
        workspace = appkit.NSWorkspace.sharedWorkspace()
        notification = appkit.NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification
        workspace.notificationCenter().postNotificationName_object_(notification, workspace)
        motion.assert_called_once_with(native.reduced_motion)
        context = AppContext('', '', AppType.OTHER)
        for phase, mode in [('listening', 'batch'), ('processing', 'batch'), ('processing', 'whisper_cpp')]:
            native.show(Snapshot(1, 1, phase, mode, context))
            native.draw(0.7, 1, 1)
            native.view.display()
            assert panel.isVisible() and not panel.isKeyWindow() and not panel.isMainWindow()
            # Exercise rasterization as well as the deferred draw callback.
            bitmap = native.view.bitmapImageRepForCachingDisplayInRect_(native.view.bounds())
            native.view.cacheDisplayInRect_toBitmapImageRep_(native.view.bounds(), bitmap)
            data = bitmap.representationUsingType_properties_(appkit.NSBitmapImageFileTypePNG, {})
            assert len(bytes(data)) > 1000
        assert not errors
        native.timer(lambda: None)
        timer = native._timer
        assert timer.isValid()
        native.close()
        assert not timer.isValid() and native._timer is None
        assert native._observers == [] and native.panel is None
        workspace.notificationCenter().postNotificationName_object_(notification, workspace)
        assert motion.call_count == 1
    finally:
        native.close()


def test_native_display_removal_and_target_application_fallback(appkit):
    from vox.ui.mac.overlay import NativeOverlay, _target_bounds

    context = SimpleNamespace(win_id='missing', pid='5')
    windows = [{'kCGWindowNumber': 20, 'kCGWindowOwnerPID': 5, 'kCGWindowLayer': 0,
                'kCGWindowBounds': {'X': -1000, 'Y': 100, 'Width': 800, 'Height': 500}}]
    with patch('Quartz.CGWindowListCopyWindowInfo', return_value=windows):
        assert _target_bounds(context, 900) == (-1000, 300, 800, 500)
    native = NativeOverlay()
    try:
        screens = [(1, (0, 0, 1440, 900), (0, 0, 1440, 875)),
                   (2, (-1920, 0, 1920, 1080), (-1920, 0, 1920, 1055))]
        with patch('vox.ui.mac.overlay._screens', return_value=screens), \
             patch('vox.ui.mac.overlay._target_bounds', return_value=(-1000, 300, 800, 500)):
            native.show(Snapshot(1, 1, 'listening', 'batch', context))
            assert native.screen_id == 2
        with patch('vox.ui.mac.overlay._screens', return_value=screens[:1]):
            native._display_changed()
            assert native.screen_id == 1
    finally:
        native.close()


def test_native_rejects_background_thread(appkit):
    from vox.ui.mac.overlay import NativeOverlay

    errors = []
    def build():
        try:
            NativeOverlay()
        except RuntimeError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=build)
    thread.start()
    thread.join()
    assert errors == ['The recording panel requires the main thread']


def test_native_close_attempts_remaining_cleanup_after_exceptions(appkit):
    import pytest

    from vox.ui.mac.overlay import NativeOverlay

    native = NativeOverlay.__new__(NativeOverlay)
    native._closed = False
    native.timer = Mock(side_effect=RuntimeError('timer'))
    first, second, panel = Mock(), Mock(), Mock()
    first.removeObserver_.side_effect = RuntimeError('observer')
    panel.orderOut_.side_effect = RuntimeError('order out')
    native._observers = [(first, 1), (second, 2)]
    native.panel = panel
    native.view = SimpleNamespace(owner=native)
    with pytest.raises(RuntimeError, match='timer'):
        native.close()
    second.removeObserver_.assert_called_once_with(2)
    panel.close.assert_called_once()
    assert native.panel is None and native.view.owner is None and not native._observers


def test_cancel_button_is_nonactivating_and_hides_with_panel(appkit):
    from vox.ui.mac.overlay import NativeOverlay
    native = NativeOverlay()
    native.on_cancel = Mock()
    try:
        native.show(Snapshot(7, 1, 'listening'))
        assert native.cancel_panel.isVisible()
        assert not native.cancel_panel.canBecomeKeyWindow()
        assert not native.cancel_panel.ignoresMouseEvents()
        assert native.cancel_panel.sharingType() == appkit.NSWindowSharingNone
        native.cancel_button.performClick_(None)
        native.on_cancel.assert_called_once_with(7)
        native.cancel_button.generation = 7  # pressed before a new recording appeared
        native.show(Snapshot(8, 1, 'processing'))
        native.cancel_button.cancel_(None)
        assert native.on_cancel.call_args.args == (7,)
        native.hide()
        assert not native.cancel_panel.isVisible() and not native.panel.isVisible()
        native.show(Snapshot(8, 2, 'fading'))
        assert not native.cancel_panel.isVisible()
    finally:
        native.close()
    assert native.cancel_panel is None


def test_partial_cancel_panel_construction_still_closes_body(appkit):
    from vox.ui.mac.overlay import NativeOverlay
    native = NativeOverlay.__new__(NativeOverlay)
    native._closed = False
    native.timer = Mock()
    native._observers = []
    body, button_panel = Mock(), Mock()
    native.panel, native.cancel_panel = body, button_panel
    native.view = SimpleNamespace(owner=native)
    native.close()
    body.close.assert_called_once()
    button_panel.close.assert_called_once()
    assert native.view.owner is None
