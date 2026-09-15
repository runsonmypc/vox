## 1. Dependencies and History Persistence

- [ ] 1.1 Add `pystray>=0.19.5` and `Pillow>=10.0.0` to `pyproject.toml` dependencies and verify installation via `uv pip list`
- [ ] 1.2 Implement `HistoryDB` in `vox/history.py` with SQLite schema creation, append record, and search queries, and verify with unit tests in `tests/test_history.py`
- [ ] 1.3 Hook `HistoryDB.insert()` into `vox/daemon.py` upon successful text paste, and verify records are saved during dictation turns

## 2. System Tray & Status Integration

- [ ] 2.1 Implement procedural icon generation in `vox/ui/icons.py` using `Pillow` for Idle (monochrome) and Recording (red dot) states, and verify image buffers are generated
- [ ] 2.2 Implement `TrayManager` in `vox/ui/tray.py` running in a dedicated thread with thread-safe state dispatch via `loop.call_soon_threadsafe`, and verify state changes update the icon
- [ ] 2.3 Add tray context menu items for current state, audio input device selector (`sounddevice.query_devices`), pause/resume toggle, and quick copy of the 3 most recent dictations
- [ ] 2.4 Integrate `TrayManager` into `vox/daemon.py` lifecycle and verify tray reflects `IDLE`, `RECORDING`, and `PROCESSING` transitions

## 3. History Search & Clipboard Recovery UI

- [ ] 3.1 Implement the history search drawer/window in `vox/ui/history_window.py` to list and filter records from `HistoryDB`, and verify UI search filtering
- [ ] 3.2 Add 1-click clipboard copy action to history items and verify text is placed on the system clipboard
- [ ] 3.3 Wire the history window trigger to the tray menu item and verify it opens and closes cleanly without leaking memory

## 4. Visual Vocabulary & Snippets Management

- [ ] 4.1 Implement `update_dictionary` and `update_snippet` helper functions in `vox/config.py` using atomic file writes, and verify unit tests in `tests/test_config_mutation.py`
- [ ] 4.2 Implement vocabulary and snippet editor interface in `vox/ui/vocab_window.py` to list, add, and remove terms and snippets
- [ ] 4.3 Wire vocabulary and snippet actions to update `config.toml` and verify `_config_reloader` detects changes and hot-reloads them into the active daemon

## 5. Integration Verification

- [ ] 5.1 Add mock integration tests in `tests/test_tray_integration.py` covering daemon state propagation to tray and database persistence
- [ ] 5.2 Verify graceful fallback when running in headless environments without a display or notification server
