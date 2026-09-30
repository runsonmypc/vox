"""Subprocesses used by recovery_desktop.py: real native windows and daemon loop."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['daemon', 'history', 'target'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('name')
    args = parser.parse_args()
    directory, name = args.directory, args.name
    logging.basicConfig(level=logging.INFO)
    import vox.control

    vox.control._lock_dir = lambda: directory / 'runtime'
    if args.role == 'daemon':
        asyncio.run(daemon(directory, name))
    else:
        window(directory, name, args.role)


async def daemon(directory, name):
    from vox.config import load_config
    from vox.daemon import _Daemon
    from vox.retry import RetryController

    original_external = RetryController.external

    def traced_external(self, context):
        result = original_external(self, context)
        logging.info('Native focus gate: pid=%s class=%s external=%s', context.pid, context.wm_class, result)
        return result
    RetryController.external = traced_external
    logging.getLogger('vox.injector').setLevel(logging.DEBUG)
    config = load_config(directory / 'config.toml')
    config.openai_api_key = 'sk-integration-placeholder'
    # Exercise the production state machine, providers, converters, control, storage,
    # focus detection and injector. Only microphone/hotkey/sound hardware is replaced.
    with patch('vox.daemon.HotkeyListener', return_value=MagicMock()), \
         patch('vox.daemon.Recorder', return_value=MagicMock()), \
         patch('vox.daemon.SoundPlayer', return_value=MagicMock()), \
         patch('vox.history.DEFAULT_HISTORY_PATH', directory / 'history.db'):
        owner = vox_lock(directory)
        daemon = _Daemon(config, None)
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0.2)
        atomic_json(directory / f'{name}.state', {'ready': True, 'pid': os.getpid()})
        cursor = 0
        try:
            while not task.done():
                commands, cursor = read_commands(directory, name, cursor)
                for command in commands:
                    if command['action'] == 'pause':
                        daemon.queue.put_nowait('pause')
                    elif command['action'] == 'stop':
                        task.cancel()
                await asyncio.sleep(0.05)
            await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            os.close(owner)


def vox_lock(directory):
    from vox.__main__ import _acquire_instance_lock

    fd = _acquire_instance_lock(directory / 'runtime')
    if fd is None:
        raise RuntimeError('Integration daemon ownership lock unavailable')
    return fd


def read_commands(directory, name, cursor):
    path = directory / f'{name}.commands'
    lines = path.read_text().splitlines() if path.exists() else []
    return [json.loads(line) for line in lines[cursor:]], len(lines)


def window(directory, name, role):
    from vox.history import HistoryDB
    from vox.ui.history_model import HistoryModel

    db = HistoryDB(directory / 'history.db') if role == 'history' else None
    model = HistoryModel(db) if db is not None else None
    cursor = 0
    if sys.platform == 'darwin':
        import AppKit
        import objc
        from PyObjCTools import AppHelper

        from vox.ui.mac import kit
        from vox.ui.mac.history import HistoryController

        class TargetTextView(AppKit.NSTextView):
            def keyDown_(self, event):
                logging.info('Target key event: code=%s chars=%r flags=%s', event.keyCode(), event.characters(), event.modifierFlags())
                objc.super(TargetTextView, self).keyDown_(event)

            def paste_(self, sender):
                logging.info('Target paste action')
                objc.super(TargetTextView, self).paste_(sender)

        app = kit.application()
        if role == "target":
            app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        if role == 'history':
            controller = HistoryController.alloc().initWithModel_(model)
            win = controller.window
            text = controller.text_view
        else:
            win = kit.window(f'Vox integration target {name}', (600, 300), (360, 200))
            text = TargetTextView.alloc().initWithFrame_(((0, 0), (600, 300)))
            win.setContentView_(text)
        win.makeKeyAndOrderFront_(None)
        win.makeFirstResponder_(text)
        app.activateIgnoringOtherApps_(True)

        def focus():
            app.unhide_(None)
            win.deminiaturize_(None)
            win.makeKeyAndOrderFront_(None)
            win.makeFirstResponder_(text)
            app.activateIgnoringOtherApps_(True)

        def command(command):
            action = command['action']
            if action == 'focus':
                focus()
            elif action == 'reset':
                text.setString_('')
            elif action == 'retry-local':
                controller.retry_local.performClick_(None)
            elif action == 'retry-batch':
                controller.retry_batch.performClick_(None)
            elif action == 'cancel':
                controller.cancel_retry.performClick_(None)
            elif action == 'delete':
                controller.delete_button.performClick_(None)
            elif action == 'clear':
                controller.confirm = lambda _window, _title, _message, _button, _destructive, then: then()
                controller.clearHistory_(None)
            elif action == 'copy-attempt':
                controller.attempts.selectItemAtIndex_(command.get('index', 1))
                controller.copyAttempt_(None)
            elif action == 'resize':
                win.setContentSize_((640, 400))
            elif action == 'refresh':
                controller.refresh()
            elif action == 'stop':
                app.stop_(None)
                AppHelper.stopEventLoop()

        def state():
            result = {'ready': True, 'pid': os.getpid(), 'text': str(text.string()), 'focused': bool(app.isActive())}
            if role == 'history':
                entry = controller.selected_entry()
                result.update(local=bool(controller.retry_local.isEnabled()), batch=bool(controller.retry_batch.isEnabled()),
                              status=entry.record.status if entry else None, cancel_visible=not controller.cancel_retry.isHidden())
            return result

        def poll():
            nonlocal cursor
            try:
                commands, cursor = read_commands(directory, name, cursor)
                for item in commands:
                    command(item)
                atomic_json(directory / f'{name}.state', state())
            except Exception as e:
                atomic_json(directory / f'{name}.state', {'error': repr(e)})
                raise
            AppHelper.callLater(0.05, poll)
        AppHelper.callAfter(poll)
        AppHelper.runEventLoop(unexpectedErrorAlert=lambda: True)
    else:
        from vox.ui.gtk.common import Adw, Gio, GLib, Gtk, setup
        from vox.ui.gtk.history import HistoryWindow

        app = Adw.Application(application_id=f'com.vox.integration.{name}', flags=Gio.ApplicationFlags.NON_UNIQUE)
        holder = {}

        def activate(application):
            nonlocal cursor
            setup()
            if role == 'history':
                win = HistoryWindow(model, application=application)
                text = win.text
            else:
                win = Gtk.ApplicationWindow(application=application, title=f'Vox integration target {name}',
                                            default_width=600, default_height=300)
                text = Gtk.TextView()
                win.set_child(text)
            holder['win'] = win
            win.present()
            if role == 'target':
                text.grab_focus()

            def command(item):
                action = item['action']
                if action == 'focus':
                    win.unminimize()
                    win.present()
                    if role == 'target':
                        text.grab_focus()
                elif action == 'reset':
                    text.get_buffer().set_text('')
                elif action in ('retry-local', 'retry-batch', 'cancel', 'delete'):
                    button = {'retry-local': win.retry_local, 'retry-batch': win.retry_batch,
                              'cancel': win.cancel_retry, 'delete': win.delete_button}[action]
                    button.emit('clicked')
                elif action == 'clear':
                    win.confirm = lambda _window, _title, _message, _button, _destructive, then: then()
                    win.clear_history()
                elif action == 'copy-attempt':
                    button = win.attempts.get_first_child()
                    button.emit('clicked')
                elif action == 'resize':
                    win.set_default_size(380, 500)
                elif action == 'refresh':
                    win.refresh()
                elif action == 'stop':
                    application.quit()

            def poll():
                nonlocal cursor
                try:
                    commands, cursor = read_commands(directory, name, cursor)
                    for item in commands:
                        command(item)
                    if role == 'target':
                        buffer = text.get_buffer()
                        contents = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
                    else:
                        contents = text.get_text()
                    result = {'ready': True, 'pid': os.getpid(), 'text': contents, 'focused': win.is_active()}
                    if role == 'history':
                        entry = win.selected_entry()
                        result.update(local=win.retry_local.get_sensitive(), batch=win.retry_batch.get_sensitive(),
                                      status=entry.record.status if entry else None, cancel_visible=win.cancel_retry.get_visible(),
                                      narrow=win.split.get_collapsed())
                    atomic_json(directory / f'{name}.state', result)
                except Exception as e:
                    atomic_json(directory / f'{name}.state', {'error': repr(e)})
                    raise
                return GLib.SOURCE_CONTINUE
            GLib.timeout_add(50, poll)
        app.connect('activate', activate)
        app.run(None)
    if db is not None:
        db.close()


if __name__ == '__main__':
    main()
