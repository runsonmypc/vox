"""Exercise native cancel clicks under AppKit's real event loop without audio or paste."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import AppKit
    from Foundation import NSTimer
    from PyObjCTools import AppHelper
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

    from vox.ui.mac.overlay import NativeOverlay
    from vox.ui.overlay import Snapshot

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    workspace = AppKit.NSWorkspace.sharedWorkspace()
    def stop_native_loop():
        app.stop_(None)
        event = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            AppKit.NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0)
        app.postEvent_atStart_(event, False)

    original_focus = workspace.frontmostApplication().processIdentifier()
    pointer = CGEventGetLocation(CGEventCreate(None))
    native = NativeOverlay()
    calls, passed = [], []
    try:
        for generation, phase in enumerate(('listening', 'processing'), 1):
            native.show(Snapshot(generation, 1, phase))
            native.draw(0.65, 1, 1)
            frame = native.cancel_panel.frame()
            height = AppKit.NSScreen.screens()[0].frame().size.height
            point = (frame.origin.x + frame.size.width / 2, height - frame.origin.y - frame.size.height / 2)

            def cancel(value):
                calls.append(value)
                native.hide()
                stop_native_loop()

            native.on_cancel = cancel

            def post_click(point=point):
                for kind in (kCGEventLeftMouseDown, kCGEventLeftMouseUp):
                    CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kind, point, kCGMouseButtonLeft))

            deadline = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(5, False, lambda _: stop_native_loop())
            AppHelper.callLater(0.2, post_click)
            try:
                app.run()
            finally:
                deadline.invalidate()
            assert calls[-1:] == [generation], f'No cancel callback in {phase}'
            assert not native.panel.isVisible() and not native.cancel_panel.isVisible()
            assert workspace.frontmostApplication().processIdentifier() == original_focus, 'Focus changed'
            assert native.panel.ignoresMouseEvents()
            passed.append(f'Actual {phase} cancel click: correct generation, both windows hidden, original app retains focus')
    finally:
        native.close()
        CGWarpMouseCursorPosition(pointer)
    assert native.panel is None and native.cancel_panel is None
    passed.append('Both native windows released')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'passed': passed}, indent=2) + '\n')
    print(json.dumps({'passed': passed}, indent=2))


if __name__ == '__main__':
    main()
