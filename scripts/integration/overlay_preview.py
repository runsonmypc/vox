"""Render the actual platform overlay at 2x for visual comparison; no audio or provider use."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vox.ui.overlay import PANEL_SIZE, Snapshot  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'darwin':
        import AppKit

        from vox.ui.mac.overlay import NativeOverlay
        AppKit.NSApplication.sharedApplication()
        platform = 'mac'
    else:
        from vox.ui.x11.overlay import Gtk, NativeOverlay, cairo
        Gtk.init_check()
        platform = 'linux'
    width, height = PANEL_SIZE
    native = NativeOverlay()
    try:
        for phase, mode in [('listening', 'batch'), ('listening', 'whisper_cpp'),
                            ('processing', 'batch'), ('processing', 'whisper_cpp')]:
            native.show(Snapshot(1, 1, phase, mode))
            native.draw(0.65 if phase == 'listening' else 0, 1, 1)
            output = args.output / f'{platform}-{phase}-{mode}.png'
            if platform == 'mac':
                # An explicit bitmap makes both columns the same size at 2x, regardless of display scale.
                bitmap = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                    None, width * 2, height * 2, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0)
                bitmap.setSize_(PANEL_SIZE)
                AppKit.NSGraphicsContext.saveGraphicsState()
                try:
                    AppKit.NSGraphicsContext.setCurrentContext_(AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap))
                    native.view.paint()
                finally:
                    AppKit.NSGraphicsContext.restoreGraphicsState()
                data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
                output.write_bytes(bytes(data))
            else:
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * 2, height * 2)
                context = cairo.Context(surface)
                context.scale(2, 2)
                native._paint(context, width, height)
                surface.write_to_png(str(output))
    finally:
        native.close()


if __name__ == '__main__':
    main()
