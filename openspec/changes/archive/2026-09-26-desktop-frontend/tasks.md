## 1. Dependencies and History Persistence

- [x] 1.1 Add `pystray>=0.19.5`, `Pillow>=10.0.0`, and `tomlkit` to `pyproject.toml` dependencies and verify installation via `uv pip list`
- [x] 1.2 Implement `HistoryDB` in `vox/history.py` with SQLite schema creation, append record, and search queries, and verify with unit tests in `tests/test_history.py`
- [x] 1.3 Hook `HistoryDB.insert()` into `vox/daemon.py` upon successful text paste, and verify records are saved during dictation turns

## 2. System Tray & Status Integration

- [x] 2.1 Implement procedural icon generation in `vox/ui/icons.py` using `Pillow` for Idle (monochrome) and Recording (red dot) states, and verify image buffers are generated
- [x] 2.2 Implement `TrayManager` in `vox/ui/tray.py` owning the main thread (Cocoa / GTK) while the asyncio daemon runs in a dedicated thread, with thread-safe dispatch via `loop.call_soon_threadsafe` (tray → daemon) and `AppHelper.callAfter` / `GLib.idle_add` (daemon → tray), and verify state changes update the icon
- [x] 2.3 Add tray context menu items for current state, audio input device selector (`sounddevice.query_devices`), pause/resume toggle, and quick copy of the 3 most recent dictations
- [x] 2.4 Integrate `TrayManager` into `vox/daemon.py` lifecycle and verify tray reflects `IDLE`, `RECORDING`, and `PROCESSING` transitions
- [x] 2.5 Add a transcription submenu that switches between OpenAI batch, OpenAI streaming, and local whisper.cpp while idle, persists the choice, and disables unavailable modes

## 3. History Search & Clipboard Recovery UI

- [x] 3.1 Implement the history search drawer/window in `vox/ui/history_window.py` to list and filter records from `HistoryDB`, and verify UI search filtering
- [x] 3.2 Add 1-click clipboard copy action to history items and verify text is placed on the system clipboard
- [x] 3.3 Wire the history window trigger to the tray menu item and verify it opens and closes cleanly without leaking memory

## 4. Visual Vocabulary & Snippets Management

- [x] 4.1 Implement `update_dictionary` and `update_snippet` helper functions in `vox/config.py` using `tomlkit` and atomic file writes, and verify unit tests in `tests/test_config_mutation.py`
- [x] 4.2 Implement vocabulary and snippet editor interface in `vox/ui/vocab_window.py` to list, add, and remove terms and snippets
- [x] 4.3 Wire vocabulary and snippet actions to update `config.toml` and verify `_config_reloader` detects changes and hot-reloads them into the active daemon

## 5. Integration Verification

- [x] 5.1 Add mock integration tests in `tests/test_tray_integration.py` covering daemon state propagation to tray and database persistence
- [x] 5.2 Verify graceful fallback when running in headless environments without a display or tray support

## 6. Linux Tray Parity & Installer

- [x] 6.1 Enable the Linux tray through pystray's AppIndicator backend with `GLib.idle_add` dispatch, light idle/paused glyphs, and a notice when no StatusNotifierItem host is running
- [x] 6.2 Replace source-only `webrtcvad` with the prebuilt drop-in `webrtcvad-wheels` so installs need no compiler
- [x] 6.3 Add `install.sh` for macOS and Linux: isolated virtualenv, missing system packages, GNOME AppIndicator extension, API key, and login service
- [x] 6.4 Verify on Linux: unit suite under Xvfb, StatusNotifierItem/DBusMenu end-to-end against a stand-in tray host and the real GNOME extension, and a real install
- [x] 6.5 Add a Vox launcher (`Vox.app` in `/Applications` on macOS, `vox.desktop` on Linux) that starts the login service, with an app icon
- [x] 6.6 Check for a running instance before config and permission prompts, and exit 0 so launchd/systemd do not retry
- [x] 6.7 Replace the generic microphone with the cog-microphone glyph (grille slots lit by state) and a matching launcher icon
