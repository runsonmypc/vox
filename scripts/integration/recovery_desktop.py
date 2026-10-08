"""Real AppKit/GTK recovery, focus, keypress and clipboard integration.

Run on a logged-in macOS GUI with Accessibility permission, or on Linux under
Openbox + Xvfb. Audio input/hotkeys/sounds and provider responses are controlled.
Synthetic audio bypasses VAD; partial attempts are seeded, native controls are
activated programmatically, and the Clear confirmation is accepted by the fixture.
The daemon, native History buttons, Unix socket, converters, providers, focus
lookup, clipboard and paste keystrokes execute production code across processes.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
WORKER = Path(__file__).with_name('desktop_worker.py')
TEXT = 'Recovered integration words.'
SENTINEL = 'Vox integration clipboard sentinel'


def wait(check, description, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sys.platform == 'darwin':
            from Foundation import NSDate, NSRunLoop

            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(f'Timed out: {description}')


class Desktop:
    def __init__(self, directory):
        self.directory = directory
        self.processes = {}
        self.logs = {}
        self.results = []
        self.env = {**os.environ, 'PYTHONPATH': str(ROOT), 'OPENAI_API_KEY': 'sk-integration-placeholder',
                    'NO_PROXY': '127.0.0.1,localhost', 'PYSTRAY_BACKEND': 'dummy'}

    def launch(self, role, name):
        log = open(self.directory / f'{name}.log', 'w')
        self.logs[name] = log
        self.processes[name] = subprocess.Popen([sys.executable, str(WORKER), role, str(self.directory), name],
                                                env=self.env, stdout=log, stderr=log)
        wait(lambda: self.state(name).get('ready'), f'{name} starts')

    def state(self, name):
        proc = self.processes.get(name)
        if proc is not None and proc.poll() is not None:
            raise AssertionError(f'{name} exited (status={proc.returncode}): {(self.directory / f"{name}.log").read_text()[-4000:]}')
        path = self.directory / f'{name}.state'
        try:
            state = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        if 'error' in state:
            raise AssertionError(f'{name}: {state["error"]}')
        return state

    def command(self, name, action, **fields):
        with open(self.directory / f'{name}.commands', 'a') as commands:
            commands.write(json.dumps({'action': action, **fields}) + '\n')

    def focus(self, name):
        from vox.config import Config
        from vox.window import detect_paste_target

        self.command(name, 'focus')
        pid = str(self.state(name)['pid'])
        if sys.platform != 'darwin':
            title = '^History$' if name == 'history' else f'^Vox integration target {name}$'
            windows = subprocess.run(['xdotool', 'search', '--all', '--pid', pid, '--name', title],
                                     check=True, capture_output=True, text=True).stdout.splitlines()
            subprocess.run(['xdotool', 'windowmap', windows[-1], 'windowactivate', '--sync', windows[-1]], check=True)
        def focused():
            if sys.platform == 'darwin':
                from Foundation import NSDate, NSRunLoop

                NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))
            return detect_paste_target(Config()).pid == pid
        try:
            wait(focused, f'{name} receives real desktop focus')
        except AssertionError:
            print('Focus diagnostics:', self.state(name), detect_paste_target(Config()), file=sys.stderr)
            if sys.platform == 'darwin':
                subprocess.run([sys.executable, '-c', 'from AppKit import NSWorkspace; print(NSWorkspace.sharedWorkspace().frontmostApplication())'])
            raise

    def reset_target(self, name='targeta'):
        self.command(name, 'reset')
        wait(lambda: self.state(name).get('text') == '', f'{name} clears')

    def stop(self, name):
        proc = self.processes.pop(name, None)
        if proc is None:
            return
        self.command(name, 'stop')
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.logs.pop(name).close()
        for suffix in ('commands', 'state'):
            (self.directory / f'{name}.{suffix}').unlink(missing_ok=True)

    def passed(self, scenario):
        self.results.append(scenario)
        print(f'PASS {scenario}', flush=True)

    def close(self):
        for name in list(self.processes):
            self.stop(name)


def original_wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x10\x00' * 48000)
    return out.getvalue()


def prepare_provider(directory):
    script = directory / 'whisper-cli'
    script.write_text(f'#!{sys.executable}\n' + '''import json, pathlib, sys, time, wave
root = pathlib.Path(__file__).parent
settings = json.loads((root / 'provider.json').read_text())
args = sys.argv[1:]
with wave.open(args[args.index('-f') + 1], 'rb') as wav:
    record = {'provider': 'local', 'rate': wav.getframerate(), 'frames': wav.getnframes(), 'args': args}
with (root / 'provider.calls').open('a') as calls:
    calls.write(json.dumps(record) + '\\n')
time.sleep(settings['delay'])
if settings['fail']:
    sys.exit(1)
pathlib.Path(args[args.index('-of') + 1] + '.txt').write_text(settings['text'])
''')
    script.chmod(0o700)
    (directory / 'model.bin').write_bytes(b'controlled integration model')
    configure_provider(directory, fail=True)
    (directory / 'config.toml').write_text(
        f'[transcription]\nmode = "whisper_cpp"\nkeep_failed_audio = true\n'
        f'[whisper_cpp]\nbinary = {json.dumps(str(script))}\nmodel = {json.dumps(str(directory / "model.bin"))}\n'
        '[sounds]\nenabled = false\n[context]\nscreen = true\n'
    )


def configure_provider(directory, *, fail=False, delay=0):
    temporary = directory / 'provider.tmp'
    temporary.write_text(json.dumps({'fail': fail, 'delay': delay, 'text': TEXT}))
    temporary.replace(directory / 'provider.json')


def provider_calls(directory):
    path = directory / 'provider.calls'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def start_http(directory):
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == '/v1/audio/transcriptions'
            body = self.rfile.read(int(self.headers['Content-Length']))
            # Actual SDK multipart upload; verify complete converted WAV reaches the HTTP boundary.
            offset = body.index(b'RIFF')
            size = int.from_bytes(body[offset + 4:offset + 8], 'little') + 8
            with wave.open(io.BytesIO(body[offset:offset + size]), 'rb') as audio:
                record = {'provider': 'batch', 'rate': audio.getframerate(), 'frames': audio.getnframes()}
            assert b'Forbidden Original Title' not in body and b'Forbidden Screen Words' not in body
            with (directory / 'provider.calls').open('a') as calls:
                calls.write(json.dumps(record) + '\n')
            settings = json.loads((directory / 'provider.json').read_text())
            time.sleep(settings['delay'])
            data = json.dumps({'text': settings['text']}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def seed_failure(directory, db):
    from vox.config import load_config
    from vox.daemon import _process
    from vox.whisper_cpp import WhisperCppTranscriber
    from vox.window import AppContext, AppType

    config = load_config(directory / 'config.toml')
    configure_provider(directory, fail=True)
    with patch('vox.daemon.has_speech', return_value=True):
        asyncio.run(_process(original_wav(), config, WhisperCppTranscriber(config), None, None, MagicMock(),
                             asyncio.Queue(), AppContext('integration', 'Forbidden Original Title', AppType.EDITOR,
                                                         screen_text='Forbidden Screen Words'), None, 'whisper_cpp', history=db,
                             created_at='2026-09-30 12:00:00'))
    rec = db.search()[0]
    assert rec.status == 'failed' and rec.audio_id
    from vox.recovery import RecoveryStore

    assert RecoveryStore(db).read(rec.audio_id) == original_wav()
    return rec


def run(desktop):
    from vox.config import Config
    from vox.history import HistoryDB
    from vox.injector import set_clipboard
    from vox.recovery import RecoveryStore
    from vox.window import detect_paste_target

    directory = desktop.directory
    db = HistoryDB(directory / 'history.db')
    http = start_http(directory)
    desktop.env['OPENAI_BASE_URL'] = f'http://127.0.0.1:{http.server_port}/v1'
    try:
        rec = seed_failure(directory, db)
        desktop.launch('daemon', 'daemon')
        # A genuine process restart before recovery. No automatic provider call or paste is allowed.
        desktop.stop('daemon')
        desktop.launch('daemon', 'daemon')
        assert len(provider_calls(directory)) == 1
        desktop.launch('target', 'targeta')
        desktop.focus('targeta')
        desktop.launch('history', 'history')
        wait(lambda: desktop.state('history').get('local'), 'Local retry enabled')
        desktop.passed('failed capture persists across daemon restart without automatic retry')

        def retry(mode='local', delay=0):
            configure_provider(directory, delay=delay)
            desktop.reset_target()
            set_clipboard(SENTINEL)
            desktop.focus('targeta')
            desktop.command('history', 'refresh')
            desktop.focus('history')
            wait(lambda: desktop.state('history').get(mode), f'{mode} retry enabled')
            count = len(provider_calls(directory))
            desktop.command('history', f'retry-{mode}')
            wait(lambda: len(provider_calls(directory)) > count, 'real provider invoked after focus release')
            wait(lambda: detect_paste_target(Config()).pid != str(desktop.state('history')['pid']),
                 'History yielded real focus before provider execution')

        retry()
        wait(lambda: db.get(rec.id).status == 'completed', 'original row completed')
        wait(lambda: desktop.state('targeta')['text'] == TEXT, 'real paste reaches target text view')
        assert db.get(rec.id).created_at == rec.created_at and len(db.search()) == 1
        assert (directory / 'audio' / rec.audio_id).exists()  # kept for another retry until newer ones push it out
        wait(lambda: clipboard() == SENTINEL, 'clipboard restoration after native keystroke')
        desktop.passed('instant Local retry yields History focus, updates one row, pastes once and restores clipboard')

        rec = seed_failure(directory, db)
        retry('batch')
        wait(lambda: desktop.state('targeta')['text'] == TEXT, 'batch paste arrives')
        wait(lambda: clipboard() == SENTINEL, 'batch clipboard restoration')
        assert db.get(rec.id).transcription_mode == 'batch'
        assert provider_calls(directory)[-1] == {'provider': 'batch', 'rate': 16000, 'frames': 16000}
        desktop.passed('cross-method OpenAI Batch retry uploads full converted audio to loopback provider and pastes')

        rec = seed_failure(directory, db)
        retry(delay=1.5)
        desktop.focus('history')
        wait(lambda: db.get(rec.id).status == 'completed', 'completion while History has focus')
        time.sleep(0.6)
        assert desktop.state('targeta')['text'] == ''
        wait(lambda: desktop.state('history')['text'] == TEXT, 'History shows recovered text after row update')
        desktop.passed('reopened History suppresses real paste and refreshes existing row')

        desktop.launch('target', 'targetb')
        rec = seed_failure(directory, db)
        retry(delay=1.5)
        desktop.focus('targetb')
        wait(lambda: desktop.state('targetb')['text'] == TEXT, 'current external app receives paste')
        assert desktop.state('targeta')['text'] == ''
        desktop.passed('switching external apps during retry sends the actual paste to the current target')

        rec = seed_failure(directory, db)
        desktop.command('daemon', 'pause')
        retry(delay=3)
        desktop.focus('history')
        wait(lambda: desktop.state('history').get('cancel_visible'), 'Cancel Retry visible while hotkeys paused')
        desktop.command('history', 'cancel')
        wait(lambda: db.get(rec.id).status == 'failed', 'cancel restores failure')
        assert RecoveryStore(db).read(rec.audio_id) == original_wav()
        assert desktop.state('targeta')['text'] == ''
        desktop.passed('native Cancel Retry works with hotkeys paused and preserves original audio')

        retry(delay=3)
        desktop.focus('history')
        desktop.command('history', 'delete')
        wait(lambda: db.get(rec.id) is None, 'native delete cancels active retry and removes row')
        assert not (directory / 'audio' / rec.audio_id).exists()
        assert desktop.state('targeta')['text'] == ''
        desktop.passed('native Delete cancels retry and removes audio without a late paste')

        rec = seed_failure(directory, db)
        # Add a real partial attempt for the native separately-copyable action.
        db.fail_retry(rec.id, rec.revision, 'batch', 'Controlled partial failure.', 'Partial integration words.')
        desktop.command('history', 'refresh')
        desktop.focus('history')
        wait(lambda: desktop.state('history')['text'] == 'Partial integration words.', 'partial preview refresh')
        desktop.command('history', 'copy-attempt')
        wait(lambda: clipboard() == 'Partial integration words.', 'native partial-attempt copy reaches clipboard')
        if sys.platform != 'darwin':
            desktop.command('history', 'resize')
            wait(lambda: desktop.state('history').get('narrow'), 'GTK narrow layout collapses')
        desktop.passed('partial attempt is separately copyable through native controls; narrow layout checked on GTK')

        path = directory / 'config.toml'
        path.write_text(path.read_text().replace('keep_failed_audio = true', 'keep_failed_audio = false'))
        wait(lambda: not desktop.state('history').get('local') and not desktop.state('history').get('batch'),
             'hot reload disables native retry controls')
        assert RecoveryStore(db).read(rec.audio_id) == original_wav()
        path.write_text(path.read_text().replace('keep_failed_audio = false', 'keep_failed_audio = true'))
        wait(lambda: desktop.state('history').get('local') and desktop.state('history').get('batch'),
             're-enabling restores native retry controls')
        desktop.passed('live retention opt-out and re-enable update both native retry controls')

        metadata = dict(text='', original_mode='batch', attempted_mode='batch',
                        error_summary='Transcription failed. Check the selected provider and retry.',
                        created_at=rec.created_at, duration_seconds=1, app_type='EDITOR')
        store = RecoveryStore(db)
        unindexed = store.stage(original_wav(), metadata)
        store.publish(unindexed)
        desktop.command('history', 'clear')
        wait(lambda: not db.search(), 'native Clear removes all entries')
        wait(lambda: not list((directory / 'audio').iterdir()), 'native Clear removes indexed and unindexed audio')
        desktop.passed('native Clear removes all text, partial attempts, indexed and unindexed recordings')
    finally:
        if sys.exc_info()[0] is not None:
            for name in desktop.processes:
                path = directory / f'{name}.state'
                if path.exists():
                    print(f'{name} final state: {path.read_text()}', file=sys.stderr)
        desktop.close()
        http.shutdown()
        http.server_close()
        db.close()


def clipboard():
    if sys.platform == 'darwin':
        from AppKit import NSPasteboard, NSPasteboardTypeString

        return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
    return subprocess.run(['xclip', '-selection', 'clipboard', '-o'], capture_output=True, timeout=3, check=True).stdout.decode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    manager = None
    original_clipboard = None
    original_focus = None
    if sys.platform == 'darwin':
        from AppKit import NSWorkspace

        from vox.injector import _general_pasteboard, _restore_pasteboard, _snapshot_pasteboard, check_accessibility_permission

        if not check_accessibility_permission():
            raise RuntimeError('Real paste test requires macOS Accessibility permission')
        original_focus = NSWorkspace.sharedWorkspace().frontmostApplication()
        original_clipboard = _snapshot_pasteboard(_general_pasteboard())
    else:
        manager = subprocess.Popen(['openbox'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
    with tempfile.TemporaryDirectory(prefix='vox-desktop-', dir='/tmp') as directory:
        desktop = Desktop(Path(directory))
        prepare_provider(desktop.directory)
        try:
            run(desktop)
            report = {'platform': sys.platform, 'passed': len(desktop.results), 'scenarios': desktop.results,
                      'providers': 'controlled whisper-cli executable and loopback OpenAI-compatible HTTP endpoint',
                      'desktop': 'real AppKit GUI' if sys.platform == 'darwin' else 'Ubuntu GTK 4 + Openbox + Xvfb',
                      'native_focus_clipboard_and_keypress': True}
            if args.report:
                args.report.write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2), flush=True)
        except Exception:
            import shutil

            saved = Path('/tmp') / f'vox-desktop-{sys.platform}-failure'
            saved.mkdir(exist_ok=True)
            for path in desktop.directory.iterdir():
                if path.is_file() and path.suffix in ('.state', '.log', '.commands'):
                    shutil.copyfile(path, saved / path.name)
            for path in desktop.directory.glob('*.log'):
                print(f'--- {path.name} ---\n{path.read_text()[-4000:]}', file=sys.stderr)
            raise
        finally:
            desktop.close()
            if sys.platform == 'darwin':
                pb = _general_pasteboard()
                # Restore all original clipboard types if the test still owns its final value.
                if clipboard() in (SENTINEL, TEXT, 'Partial integration words.'):
                    _restore_pasteboard(pb, original_clipboard, pb.changeCount())
                if original_focus is not None:
                    original_focus.activateWithOptions_(0)
            elif manager is not None:
                manager.terminate()
                manager.wait(timeout=3)


if __name__ == '__main__':
    main()
