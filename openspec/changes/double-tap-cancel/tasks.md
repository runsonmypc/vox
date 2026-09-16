## 1. Configuration and Audio Feedback Support

- [x] 1.1 Add `double_tap_timeout_ms` (default 400ms) to `Config` in `vox/config.py` and parse it from the `[hotkey]` table; verify with unit tests in `tests/test_config.py`.
- [x] 1.2 Add `"cancel"` sound support to `SoundPlayer` in `vox/sounds.py` using macOS system alert sound (`"Blow"`) on Darwin and synthetic descending tone fallback on Linux; verify with unit tests in `tests/test_sounds.py`.

## 2. Hotkey Double-Tap Detection

- [ ] 2.1 Update `HotkeyListener` in `vox/hotkey.py` to track release timestamps, dispatch `"cancel"` when consecutive releases occur within `double_tap_timeout_ms`, and reset sequence tracking on intervening key presses or timeout expiry; verify with new tests in `tests/test_hotkey.py`.

## 3. Daemon State Machine Cancellation

- [ ] 3.1 Implement `"cancel"` handling in `vox/daemon.py` for `State.RECORDING` to stop audio capture, discard frames, cancel streaming workers/screen capture, restore volume attenuation, play `"cancel"` sound, and reset to `State.IDLE`; verify with tests in `tests/test_daemon.py`.
- [ ] 3.2 Implement `"cancel"` handling in `vox/daemon.py` for `State.PROCESSING` to cancel in-flight `process_task` and streaming connections, suppress paste injection, restore volume attenuation, play `"cancel"` sound, and reset to `State.IDLE`; verify with tests in `tests/test_daemon.py`.

## 4. Verification and Regression Testing

- [ ] 4.1 Run the full test suite via `uv run pytest` to verify all new and existing tests pass cleanly without regression.
