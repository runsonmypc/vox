"""Render native, transparent overlay frames for the README app demonstration (macOS)."""
import math
import sys
from pathlib import Path

import AppKit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vox.ui.mac.overlay import NativeOverlay
from vox.ui.overlay import PANEL_SIZE, Envelope, Snapshot

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
native = NativeOverlay()
width, height = PANEL_SIZE
try:
    for phase in ('listening', 'processing'):
        native.snapshot = Snapshot(1, 1, phase, 'batch')
        envelope = Envelope()
        for index in range(80):
            t = index / 20
            level = 0 if t < .25 or 1.3 < t < 1.6 or 2.8 < t < 3.1 else .012 + .010 * math.sin(t * 13) ** 2
            native.amplitude = envelope.sample(t, (1, t, level), 1) if phase == 'listening' else 0
            native.phase = t
            bitmap = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                None, width * 2, height * 2, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0)
            bitmap.setSize_(PANEL_SIZE)
            AppKit.NSGraphicsContext.saveGraphicsState()
            try:
                AppKit.NSGraphicsContext.setCurrentContext_(AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap))
                native.view.paint()
            finally:
                AppKit.NSGraphicsContext.restoreGraphicsState()
            png = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
            (out / f'{phase}-{index:03}.png').write_bytes(bytes(png))
finally:
    native.close()
print(f'160 native overlay frames: {out}')
