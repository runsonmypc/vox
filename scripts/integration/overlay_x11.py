"""Real X11 overlay, focus, click-through, paste and screenshot validation under Openbox.

Uses controlled levels and dedicated apps; it never records a microphone or contacts a provider.
Run in a disposable X11 desktop, e.g. the repository's integration Docker image.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if not sys.platform.startswith('linux') or not os.environ.get('DISPLAY'):
        raise SystemExit('Run this harness in a disposable Linux X11 desktop')

    import recovery_desktop
    from recovery_desktop import Desktop, clipboard

    from vox.config import Config
    from vox.injector import paste, set_clipboard
    from vox.ui.overlay import PANEL_SIZE, create_overlay
    from vox.ui.tray import _glib_dispatch
    from vox.ui.x11.overlay import GLib, Gtk
    from vox.window import _read_ocr, detect_active_window

    Gtk.init_check()
    context = GLib.MainContext.default()
    def wait(check, description, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            result = check()
            if result:
                return result
            time.sleep(0.01)
        raise AssertionError(f'Timed out: {description}')
    recovery_desktop.wait = wait

    def settle():
        deadline = time.monotonic() + 0.15
        wait(lambda: time.monotonic() >= deadline, 'X11 window events settle')

    def command(*args):
        return subprocess.check_output(args, text=True, timeout=5).strip()

    def worker(fn):
        output, errors = [], []
        def run():
            try:
                output.append(fn())
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        wait(lambda: not thread.is_alive(), 'background operation with live GLib loop')
        thread.join()
        if errors:
            raise errors[0]
        return output[0]

    overlay = create_overlay(True, _glib_dispatch)
    recorder = SimpleNamespace(latest_level=None, set_level_generation=Mock())
    config = Config()
    report = {'platform': 'Linux/X11', 'desktop': 'Ubuntu 24.04, Openbox + Xvfb',
              'audio': 'controlled microphone levels; no audio capture or provider requests', 'passed': []}
    manager = subprocess.Popen(['openbox'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    apps = []
    native = None
    with tempfile.TemporaryDirectory(prefix='vox-overlay-x11-') as directory:
        desktop = Desktop(Path(directory))
        try:
            wait(lambda: command('xprop', '-root', '_NET_SUPPORTING_WM_CHECK').find('window id') >= 0, 'window manager ready')
            desktop.launch('target', 'targeta')
            desktop.launch('target', 'targetb')
            desktop.focus('targeta')
            target = detect_active_window(config)
            assert target.win_id
            for generation, phase in enumerate(('off', 'listening', 'processing'), 1):
                desktop.reset_target()
                set_clipboard('overlay clipboard sentinel')
                if phase != 'off':
                    overlay.begin(generation)
                    overlay.listening(generation, target, 'batch', recorder)
                    if phase == 'processing':
                        overlay.processing(generation)
                    wait(lambda: overlay.backend is not None and overlay.backend.panel.get_visible(), 'overlay shown')
                assert detect_active_window(config).win_id == target.win_id
                worker(lambda: paste('Overlay target context marker.', target.app_type))
                wait(lambda: desktop.state('targeta')['text'] == 'Overlay target context marker.', 'native paste')
                wait(lambda: clipboard() == 'overlay clipboard sentinel', 'clipboard restored')
                report['passed'].append(f'focus, native paste and clipboard restoration: {phase}')
            native = overlay.backend
            desktop.focus('targetb')
            target_b = detect_active_window(config)
            worker(lambda: paste('Second target.', target_b.app_type))
            wait(lambda: desktop.state('targetb')['text'] == 'Second target.', 'paste follows app switch')
            assert overlay.backend is native
            report['passed'].append('app switching preserves target detection and text delivery')
            desktop.focus('targeta')

            # Cover the first text line, click through the panel, then paste at the new cursor.
            bounds = command('xwininfo', '-id', target.win_id)
            x = int(re.search(r'Absolute upper-left X:\s+(-?\d+)', bounds)[1])
            y = int(re.search(r'Absolute upper-left Y:\s+(-?\d+)', bounds)[1])
            native.panel.move(x, y)
            settle()
            command('xdotool', 'mousemove', str(x + 6), str(y + 8), 'click', '1')
            settle()
            worker(lambda: paste('Click probe.', target.app_type))
            wait(lambda: 'Click probe.' in desktop.state('targeta')['text'], 'click-through cursor paste')
            assert desktop.state('targeta')['text'].index('Click probe.') < 3, desktop.state('targeta')
            assert detect_active_window(config).win_id == target.win_id
            native._place()
            report['passed'].append('actual X11 mouse click passes through the panel and moves the underlying cursor')
            cancelled = []
            overlay.on_cancel = cancelled.append
            for generation, phase in ((40, 'listening'), (41, 'processing')):
                overlay.begin(generation)
                overlay.listening(generation, target, 'batch', recorder)
                if phase == 'processing':
                    overlay.processing(generation)
                wait(lambda generation=generation: native.snapshot.generation == generation, 'cancel phase applied')
                settle()
                px, py = native.panel.get_position()
                command('xdotool', 'mousemove', str(px + 213), str(py + 11), 'click', '1')
                wait(lambda generation=generation: generation in cancelled, 'native cancel click delivered')
                wait(lambda: not native.panel.get_visible(), 'panel hidden after cancel')
                assert detect_active_window(config).win_id == target.win_id
                report['passed'].append(f'actual X11 cancel click while {phase}: callback, hide, target retains focus')

            desktop.reset_target()
            worker(lambda: paste('Overlay target context marker.', target.app_type))
            wait(lambda: desktop.state('targeta')['text'] == 'Overlay target context marker.', 'capture marker ready')

            import vox.window as window_module
            original_run = window_module.subprocess.run
            captures = []
            def capture(args, **kwargs):
                if args[0] == 'maim':
                    assert not native.panel.get_visible()
                    captures.append(True)
                    original_run(['maim', '-g', region, str(args_output / f'context-hidden-{len(captures)}.png')], check=True)
                result = original_run(args, **kwargs)
                if args[0] == 'maim':
                    (args_output / f'context-{len(captures)}.png').write_bytes(result.stdout)
                elif args[0] == 'tesseract':
                    assert native.panel.get_visible() or overlay._pending
                return result
            args_output = args.output
            region = f'600x300+{x}+{y}'
            for generation in (10, 11):
                overlay.begin(generation)
                overlay.listening(generation, target, 'batch', recorder)
                if generation == 11:
                    overlay.processing(generation)
                wait(lambda generation=generation: native.snapshot.generation == generation, 'capture phase applied')
                native.panel.move(x + 100, y + 100)
                settle()
                baseline = args.output / f'context-visible-{generation}.png'
                subprocess.run(['maim', '-g', region, str(baseline)], check=True)
                # The pixel comparison below proves overlap even when page OCR skips
                # the small status inside the dark panel.
                with patch('vox.window.subprocess.run', side_effect=capture):
                    text = worker(lambda: _read_ocr(target.win_id, overlay.capture_guard))
                assert 'context marker' in text.lower(), text
                hidden_text = command('tesseract', str(args.output / f'context-hidden-{len(captures)}.png'), 'stdout').lower()
                assert 'listen' not in hidden_text and 'transcrib' not in hidden_text
                from PIL import Image, ImageStat
                panel_region = (110, 110, 100 + PANEL_SIZE[0] - 10, 100 + PANEL_SIZE[1] - 10)
                with Image.open(baseline) as visible, Image.open(args.output / f'context-hidden-{len(captures)}.png') as hidden:
                    assert max(ImageStat.Stat(visible.crop(panel_region)).mean[:3]) < 100
                    assert min(ImageStat.Stat(hidden.crop(panel_region)).mean[:3]) > 240
                assert 'listening' not in text.lower() and 'transcribing' not in text.lower()
                wait(lambda: native.panel.get_visible(), 'restored after capture')
            report['passed'].append('real maim/Tesseract capture excludes overlay in listening and fallback processing')

            for generation in range(20, 25):
                overlay.begin(generation)
                overlay.listening(generation, target, 'batch', recorder)
                wait(lambda generation=generation: native.snapshot.generation == generation, 'restart applied')
                overlay.complete(generation)
                overlay.dismiss()
            wait(lambda: not native.panel.get_visible(), 'cancel dismisses and invalidates old fades')
            assert native._timer is None
            overlay.begin(30)
            overlay.listening(30, target, 'batch', recorder)
            wait(lambda: native.panel.get_visible(), 'motion test panel shown')
            original_motion = native.settings.get_property('gtk-enable-animations')
            native.settings.set_property('gtk-enable-animations', False)
            wait(lambda: native._timer is None, 'desktop reduced motion applied')
            assert native.amplitude == 0 and native.phase == 0
            overlay.complete(30)
            wait(lambda: not native.panel.get_visible(), 'reduced-motion completion immediate')
            native.settings.set_property('gtk-enable-animations', original_motion)
            report['passed'].append('rapid restart, cancellation, GTK reduced motion and hidden timer cleanup')

            # Actual editor and terminal windows for in-use screenshots and paste verification.
            desktop.stop('targeta')
            desktop.stop('targetb')
            sample = Path(directory) / 'release_notes.txt'
            sample.write_text('Release notes\n\nThe recording overlay now works on Linux.\n\n')
            apps.append(subprocess.Popen(['mousepad', '--disable-server', str(sample)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            editor = command('xdotool', 'search', '--sync', '--name', 'release_notes.txt').splitlines()[-1]
            command('xdotool', 'windowsize', editor, '1080', '650', 'windowmove', editor, '100', '60', 'windowactivate', '--sync', editor)
            settle()
            editor_context = detect_active_window(config)
            set_clipboard('overlay clipboard sentinel')
            command('xdotool', 'key', 'ctrl+End')
            worker(lambda: paste('Dictation arrives at the cursor.', editor_context.app_type))
            assert clipboard() == 'overlay clipboard sentinel'
            command('xdotool', 'key', 'ctrl+s')
            wait(lambda: 'Dictation arrives at the cursor.' in sample.read_text(), 'actual editor receives dictation')
            for generation, (phase, mode) in enumerate((('listening', 'batch'), ('processing', 'batch'), ('processing', 'whisper_cpp')), 40):
                overlay.begin(generation)
                overlay.listening(generation, editor_context, mode, recorder)
                recorder.latest_level = (generation, time.monotonic(), 0.1)
                if phase == 'processing':
                    overlay.processing(generation)
                wait(lambda generation=generation: native.snapshot.generation == generation, 'screenshot status')
                native.timer(None)
                native.draw(0.65 if phase == 'listening' else 0, 1, 1)
                settle()
                assert detect_active_window(config).win_id == editor
                subprocess.run(['maim', str(args.output / f'desktop-{phase}-{mode}.png')], check=True)
                subprocess.run(['maim', '-i', str(native.panel.get_window().get_xid()), str(args.output / f'panel-{phase}-{mode}.png')], check=True)
            report['passed'].append('real Mousepad editor paste, clipboard restoration and three in-use screenshots')
            apps.append(subprocess.Popen(['xterm', '-title', 'Vox Overlay Terminal', '-fa', 'Monospace', '-fs', '13',
                                          '-bg', '#15171c', '-fg', '#c2c3c5', '-geometry', '100x25+100+70',
                                          '-xrm', 'XTerm*VT100.translations: #override\\n Ctrl Shift <Key>v: insert-selection(CLIPBOARD)',
                                          '-e', 'env', 'PS1=vox@linux:~$ ', 'bash', '--noprofile', '--norc'],
                                         stdout=subprocess.DEVNULL))
            terminal = command('xdotool', 'search', '--sync', '--name', '^Vox Overlay Terminal$').splitlines()[-1]
            command('xdotool', 'windowactivate', '--sync', terminal)
            settle()
            command('xdotool', 'type', '--clearmodifiers', '# ')
            settle()
            terminal_context = detect_active_window(config)
            overlay.begin(50)
            overlay.listening(50, terminal_context, 'batch', recorder)
            wait(lambda: native.snapshot.generation == 50, 'terminal overlay visible')
            native.timer(None)
            native.draw(0.65, 1, 1)
            set_clipboard('overlay clipboard sentinel')
            assert command('xdotool', 'getwindowfocus') == terminal
            assert command('xdotool', 'getactivewindow') == terminal
            # Typed markers verify that dictation lands at the live shell cursor.
            command('xdotool', 'type', '--clearmodifiers', 'BEFORE ')
            worker(lambda: paste('echo Linux overlay test', terminal_context.app_type))
            command('xdotool', 'type', '--clearmodifiers', ' AFTER')
            assert clipboard() == 'overlay clipboard sentinel'
            settle()
            # No Return is sent: this controlled command remains unexecuted at the shell prompt.
            text = worker(lambda: _read_ocr(terminal, overlay.capture_guard))
            assert 'before echo linux overlay test after' in text.lower(), text
            settle()
            subprocess.run(['maim', str(args.output / 'desktop-terminal-listening.png')], check=True)
            report['passed'].append('real XTerm paste with Ctrl+Shift+V translation restores clipboard; no command executed')

            overlay.set_enabled(False)
            wait(lambda: overlay.backend is None, 'disable cleanup')
            assert native.panel is None and native._timer is None and not native._signals and not native._monitor_signals
            overlay.set_enabled(True)
            overlay.begin(60)
            overlay.listening(60, terminal_context, 'batch', recorder)
            wait(lambda: overlay.backend is not None, 're-enabled next recording')
            overlay.close()
            wait(lambda: overlay.backend is None, 'quit cleanup')
            report['passed'].append('disable, re-enable and quit release GTK window, timer and observers')
            report['limitations'] = ['Virtual X11 display; physical mixed-DPI displays and real microphone/speech not exercised',
                                     'Openbox without compositor; opacity fade appearance depends on compositor',
                                     'Native Wayland is outside the requested X11 scope']
        finally:
            overlay.close()
            wait(lambda: overlay.backend is None, 'final backend cleanup')
            desktop.close()
            for proc in apps:
                proc.terminate()
                proc.wait(timeout=5)
            manager.terminate()
            manager.wait(timeout=5)
    (args.output / 'linux.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
