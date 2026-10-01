"""macOS overlay validation with dedicated native text targets and no cloud requests.

Runs real panel rendering, focus/paste/clipboard and guarded window capture. Saves only
synthetic target images/status previews and level statistics; microphone audio is discarded.
Does not establish human quiet/loud speech tuning, real editor/terminal or full-screen behavior.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--microphone', action='store_true', help='sample two seconds of local levels, then discard audio')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if sys.platform != 'darwin':
        raise SystemExit('This validation requires a macOS desktop')

    import AppKit
    from PIL import Image, ImageChops
    from PyObjCTools import AppHelper
    from Quartz import CGPreflightScreenCaptureAccess
    from recovery_desktop import SENTINEL, Desktop, clipboard, wait

    from vox.audio import Recorder
    from vox.config import Config
    from vox.injector import (
        _general_pasteboard,
        _restore_pasteboard,
        _snapshot_pasteboard,
        check_accessibility_permission,
        paste,
        set_clipboard,
    )
    from vox.ui.overlay import create_overlay
    from vox.window import _read_vision_ocr, detect_active_window

    if not check_accessibility_permission() or not CGPreflightScreenCaptureAccess():
        raise SystemExit('Existing Accessibility and Screen Recording permissions are required')
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    def stop_native_loop():
        app.stop_(None)
        event = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            AppKit.NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0)
        app.postEvent_atStart_(event, False)

    original_focus = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    original_clipboard = _snapshot_pasteboard(_general_pasteboard())
    config = Config(context_screen=True, overlay_enabled=True)
    recorder = SimpleNamespace(latest_level=None, set_level_generation=Mock())
    overlay = create_overlay(True, AppHelper.callAfter)
    report = {'passed': [], 'unavailable': [], 'screens': len(AppKit.NSScreen.screens())}
    owned_clipboard = {SENTINEL, 'Overlay target context marker.', 'Overlay delivery probe.', 'Click probe.'}

    def worker(fn):
        result, error = [], []
        def run():
            try:
                result.append(fn())
            except Exception as exc:
                error.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        wait(lambda: not thread.is_alive(), 'worker completes with native main loop running')
        thread.join()
        if error:
            raise error[0]
        return result[0]

    with tempfile.TemporaryDirectory(prefix='vox-overlay-', dir='/tmp') as directory:
        desktop = Desktop(Path(directory))
        try:
            desktop.launch('target', 'targeta')
            desktop.launch('target', 'targetb')
            desktop.focus('targeta')
            context = detect_active_window(config)
            assert context.win_id and context.pid == str(desktop.state('targeta')['pid'])
            # Compare text delivery with the overlay disabled, listening and processing.
            for generation, phase in enumerate(('off', 'listening', 'processing'), 1):
                desktop.reset_target()
                set_clipboard(SENTINEL)
                if phase != 'off':
                    overlay.begin(generation)
                    overlay.listening(generation, context, 'batch', recorder)
                    if phase == 'processing':
                        overlay.processing(generation)
                    wait(lambda: overlay.backend is not None and overlay.backend.panel.isVisible(), 'overlay visible')
                assert detect_active_window(config).pid == context.pid
                worker(lambda: paste('Overlay delivery probe.', context.app_type))
                wait(lambda: desktop.state('targeta')['text'] == 'Overlay delivery probe.', 'text inserted at target cursor')
                wait(lambda: clipboard() == SENTINEL, 'clipboard restored')
                report['passed'].append(f'focus, paste and clipboard restoration: overlay {phase}')

            # Switching applications must leave the panel attached to its original display.
            screen_id = overlay.backend.screen_id
            desktop.focus('targetb')
            context_b = detect_active_window(config)
            worker(lambda: paste('Overlay delivery probe.', context_b.app_type))
            wait(lambda: desktop.state('targetb')['text'] == 'Overlay delivery probe.', 'paste follows switched target')
            assert overlay.backend.screen_id == screen_id
            report['passed'].append('app switch preserves focus, paste destination and original overlay display')
            desktop.focus('targeta')
            desktop.reset_target()
            worker(lambda: paste('Overlay target context marker.', context.app_type))
            wait(lambda: desktop.state('targeta')['text'] == 'Overlay target context marker.', 'capture marker inserted')

            # Native reference previews for every status, using exactly the delivered renderer.
            for index, (phase, mode) in enumerate((('listening', 'batch'), ('processing', 'batch'), ('processing', 'whisper_cpp')), 10):
                overlay.begin(index)
                overlay.listening(index, context, mode, recorder)
                if phase == 'processing':
                    overlay.processing(index)
                wait(lambda index=index: overlay.backend.snapshot.generation == index, 'native status rendered')
                backend = overlay.backend
                backend.timer(None)
                backend.draw(0.65 if phase == 'listening' else 0, 1, 1)
                backend.view.display()
                bitmap = backend.view.bitmapImageRepForCachingDisplayInRect_(backend.view.bounds())
                backend.view.cacheDisplayInRect_toBitmapImageRep_(backend.view.bounds(), bitmap)
                data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
                (args.output / f'{phase}-{mode}.png').write_bytes(bytes(data))
            report['passed'].append('native raster previews for Listening, cloud processing and local processing')

            # Save only images of our synthetic window. The production guard surrounds the real capture.
            import vox.window as window_module
            original_run = window_module.subprocess.run
            captured = []
            def shot(command, **kwargs):
                if command[0] == 'screencapture':
                    assert not overlay.backend.panel.isVisible()
                    assert not overlay.backend.cancel_panel.isVisible()
                    captured.append(True)
                result = original_run(command, **kwargs)
                if command[0] == 'screencapture' and result.returncode == 0:
                    (args.output / f'context-{len(captured)}.png').write_bytes(Path(command[-1]).read_bytes())
                return result
            for phase in ('listening', 'processing'):
                generation = 20 if phase == 'listening' else 21
                overlay.begin(generation)
                overlay.listening(generation, context, 'batch', recorder)
                if phase == 'processing':
                    overlay.processing(21)
                wait(lambda: overlay.backend.panel.isVisible(), 'panel visible before capture')
                with patch('vox.window.subprocess.run', side_effect=shot):
                    text = worker(lambda: _read_vision_ocr(context.win_id, overlay.capture_guard))
                assert 'context marker' in text.lower(), repr(text)
                assert 'listening' not in text.lower() and 'transcribing' not in text.lower()
                wait(lambda: overlay.backend.panel.isVisible(), 'panel restored after capture')
            assert len(captured) == 2
            first, second = [Image.open(args.output / f'context-{i}.png').convert('RGB') for i in (1, 2)]
            report['capture_pixels_identical'] = ImageChops.difference(first, second).getbbox() is None
            report['capture_pixel_difference_bounds'] = ImageChops.difference(first, second).getbbox()
            report['passed'].append('actual targeted screenshot/OCR excludes overlay during listening and fallback processing')

            # Put the panel over our target's first text line and send an actual mouse event.
            # A working click-through panel lets the target move its insertion point there.
            from Quartz import (
                CGEventCreate,
                CGEventCreateMouseEvent,
                CGEventGetLocation,
                CGEventPost,
                CGWarpMouseCursorPosition,
                kCGEventLeftMouseDown,
                kCGEventLeftMouseUp,
                kCGHIDEventTap,
                kCGMouseButtonLeft,
            )

            from vox.ui.mac.overlay import _target_bounds

            primary_height = AppKit.NSScreen.screens()[0].frame().size.height
            x, y, width, height = _target_bounds(context, primary_height)
            point = (x + 10, primary_height - (y + height - 35))
            old_pointer = CGEventGetLocation(CGEventCreate(None))
            try:
                overlay.backend.panel.setFrameOrigin_((x, y + height - 63))
                for kind in (kCGEventLeftMouseDown, kCGEventLeftMouseUp):
                    CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kind, point, kCGMouseButtonLeft))
                worker(lambda: paste('Click probe.', context.app_type))
                wait(lambda: 'Click probe.' in desktop.state('targeta')['text'], 'click-through paste delivered')
                assert desktop.state('targeta')['text'].index('Click probe.') < 3
                assert detect_active_window(config).pid == context.pid
                report['passed'].append('native click-through moves the underlying target cursor and preserves focus')
            finally:
                CGWarpMouseCursorPosition(old_pointer)
                overlay.backend._place()

            # OS motion notification plumbing is unit-tested; inject the preference callback here.
            overlay._motion_changed(True)
            wait(lambda: overlay.backend._timer is None, 'reduced motion stops native timer')
            overlay.complete(21)
            wait(lambda: not overlay.backend.panel.isVisible(), 'reduced motion completion dismisses immediately')
            report['passed'].append('native static reduced-motion presentation and immediate completion')
            overlay._motion_changed(False)

            cancelled = []
            overlay.on_cancel = cancelled.append
            old_pointer = CGEventGetLocation(CGEventCreate(None))
            try:
                for generation, phase in ((40, 'listening'), (41, 'processing')):
                    overlay.begin(generation)
                    overlay.listening(generation, context, 'batch', recorder)
                    if phase == 'processing':
                        overlay.processing(generation)
                    wait(lambda: overlay.backend.cancel_panel.isVisible(), 'cancel button visible')
                    frame = overlay.backend.cancel_panel.frame()
                    point = (frame.origin.x + frame.size.width / 2,
                             primary_height - frame.origin.y - frame.size.height / 2)
                    def cancelled_click(value):
                        cancelled.append(value)
                        stop_native_loop()
                    overlay.on_cancel = cancelled_click
                    def post_click(point=point):
                        for kind in (kCGEventLeftMouseDown, kCGEventLeftMouseUp):
                            CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kind, point, kCGMouseButtonLeft))
                    from Foundation import NSTimer
                    deadline = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                        5, False, lambda _: stop_native_loop())
                    AppHelper.callLater(0.1, post_click)
                    try:
                        app.run()  # use the real application loop for native button tracking
                    finally:
                        deadline.invalidate()
                    assert generation in cancelled, 'native cancel click not delivered'
                    wait(lambda: not overlay.backend.panel.isVisible() and not overlay.backend.cancel_panel.isVisible(),
                         'both overlay windows hidden after cancel')
                    assert detect_active_window(config).pid == context.pid
                    report['passed'].append(f'actual cancel click while {phase}: callback, both windows hidden, target retains focus')
            finally:
                CGWarpMouseCursorPosition(old_pointer)

            if args.microphone:
                mic = Recorder(Config())
                levels = []
                try:
                    mic.start()
                    overlay.begin(30)
                    overlay.listening(30, context, 'batch', mic)
                    end = time.monotonic() + 2
                    def finished():
                        if mic.latest_level is not None:
                            levels.append(mic.latest_level[2])
                        return time.monotonic() >= end
                    wait(finished, 'two seconds of local microphone levels')
                finally:
                    overlay.dismiss()
                    mic.discard()
                report['microphone'] = {'samples': len(levels), 'minimum': min(levels, default=0),
                                        'maximum': max(levels, default=0), 'audio_retained': False}
                assert levels, 'No microphone callbacks received'
                report['passed'].append('real microphone callbacks drive the native overlay; recorded audio discarded')

            overlay.set_enabled(False)
            wait(lambda: overlay.backend is None, 'disable tears down native resources')
            overlay.set_enabled(True)
            overlay.begin(40)
            overlay.listening(40, context, 'batch', recorder)
            wait(lambda: overlay.backend is not None, 'next recording recreates native panel')
            native = overlay.backend
            overlay.close()
            wait(lambda: overlay.backend is None, 'quit releases backend')
            assert native.panel is None and native._timer is None and not native._observers
            report['passed'].append('disable, re-enable on next recording, and quit release native resources')
            report['unavailable'] = [
                'Human quiet/loud speech calibration and subjective motion/performance review',
                'Actual editor and terminal applications, human mouse interaction and physical hotkey input',
                'Full-screen Spaces and OS Settings reduced-motion toggle (callback tested)',
                'Physical multiple-display/mixed-scale placement: only one attached display',
                'Live cloud and whisper.cpp provider runs (lifecycle covered with controlled providers)',
            ]
        finally:
            overlay.close()
            # Drain native teardown before terminating the helper windows.
            wait(lambda: overlay.backend is None, 'final overlay cleanup')
            desktop.close()
            pb = _general_pasteboard()
            if clipboard() in owned_clipboard:
                _restore_pasteboard(pb, original_clipboard, pb.changeCount())
            if original_focus is not None:
                original_focus.activateWithOptions_(0)
    (args.output / 'desktop.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
