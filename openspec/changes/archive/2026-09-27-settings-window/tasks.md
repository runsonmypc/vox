Work on a branch from current `origin/main`. Headless tests only: GTK tests run under `dbus-run-session -- xvfb-run -a` on the Linux PC, never on its live display.

## 1. Config writers

- [x] 1.1 Add `update_audio_device`, `update_flag` (sounds, attenuation, screen), `update_attenuation_level`, `update_language`, `update_prompt` and `update_whisper_cpp` to `vox/config.py` on the existing atomic writer; `None` or empty removes the key. Verify in `tests/test_config_mutation.py`: each writes its own key and keeps other lines and comments, removal works, a new file is 0600, and a result that would not load is refused
- [x] 1.2 Make every writer skip the write when the value is unchanged. Verify with a test that setting each setting to its current value leaves a missing `config.toml` missing and an existing one byte-identical

## 2. Daemon

- [x] 2.1 Factor the reloader's apply step into `_apply_config(new)` and use it from `_config_reloader`. Verify that the existing reload tests in `test_pipeline_daemon.py` pass unchanged
- [x] 2.2 Replace `hotkey:suspend`/`hotkey:resume`/`api_key` with `settings:open` (suspend, and cancel a running recording) and `settings:closed` (resume, reload the key, and run `_apply_config` on the file at once). Verify by moving the hotkey-window daemon tests to the new events, and adding `test_settings_closed_applies_every_saved_setting_at_once` (mode, limit, sounds and hotkey changed in the file are live right after the event, with no poll)
- [x] 2.3 Stop sending the input-device list to the tray, and keep the rescan. Verify that the rescan tests still pass and that a test asserts no `set_devices` call
- [x] 2.4 Replace the daemon's `tray.open_key_window()` calls with `tray.open_settings("transcription")`. Verify with the startup-without-key and hotkey-without-key tests

## 3. Tray

- [x] 3.1 Rebuild the menu per the "Tray menu" scenario: remove the submenus and the three window items, add "Settings…" (enabled only while idle), and make the top "Set API Key…" open Settings on Transcription. Verify in `test_tray.py`: exact item order with and without a key problem, and Settings… disabled while recording or processing
- [x] 3.2 Launch `vox.ui.settings_window --config PATH [--page NAME]` with `settings:open` sent first and `settings:closed` sent on exit or on a failed launch; an open window is brought forward. Verify with `test_settings_window_suspends_dictation_until_it_closes` and `test_a_settings_window_that_fails_to_open_resumes_dictation`
- [x] 3.3 Show "Dictation is off while Settings is open" as the status line while suspended and nothing more urgent applies. Verify with a status-line ordering test
- [x] 3.4 Remove the device, limit and mode setters and `set_devices`/`_apply_devices`. Verify that ruff reports no unused code and `test_tray.py` passes

## 4. Settings model

- [x] 4.1 Add `vox/ui/settings_model.py` with `SettingsModel` (path, loaded `Config`, load error, `reload()`, stamp polling) and setters for every General and Transcription setting through the writers from 1.1. Verify with unit tests per setting, including "Couldn't Save" on `OSError` and refusal while the file does not load
- [x] 4.2 Add microphone listing via `sounddevice`, selection via `match_input_device`, "(not connected)" for a saved name that is missing, and index-to-name replacement. Verify with tests using a fake device list, covering renumbering and duplicate names
- [x] 4.3 Add mode availability through `mode_problem` with the key from `keystore`, re-checked after key and whisper.cpp path changes, with the saved mode always selectable. Verify with tests for no key, a broken whisper.cpp setup, and a setup fixed on the page
- [x] 4.4 Add `LANGUAGES`, the attenuation percent mapping (5% steps, off-step file values shown and kept), recording-limit choices with a custom value, and `~` shortening for whisper.cpp paths. Verify with unit tests
- [x] 4.5 Add `vox/ui/settings_window.py` with `--config` and `--page`, reading nothing until the window is built, and remove `vocab_window.py`, `key_window.py` and `hotkey_window.py`. Verify with `test_main_leaves_reading_the_settings_to_the_window`, and by pointing `test_isolation.py` at the new module's `DEFAULT_CONFIG_PATH`

## 5. macOS window

- [x] 5.1 Build `vox/ui/mac/settings.py`: toolbar tabs General, Hotkey, Transcription, Vocabulary, Snippets, reusing the vocabulary tab code; open on `--page`. Verify in `tests/test_settings_window.py` (macOS) that each tab exists and `--page` selects it
- [x] 5.2 Build the General tab: microphone popup with Refresh, recording-limit popup, Sounds, Lower other audio with a slider saved on release, and Screen hints with the Privacy link. Verify with tests that drive each control and read `config.toml`
- [x] 5.3 Turn the hotkey window into the Hotkey tab: save on an accepted recording, refuse a clash with nothing saved, stop recording on tab switch, and take every key (including Cmd+W) while recording. Verify by moving the macOS tests from `test_hotkey_window.py` and adding the clash and tab-switch cases
- [x] 5.4 Build the Transcription tab: mode radio group with reasons under disabled modes, language popup, prompt field saved on commit, whisper.cpp Choose… buttons with `NSOpenPanel`, and the API key row with a key-entry sheet carrying the old key window's behaviour. Verify by moving the macOS tests from `test_key_window.py`, and adding mode-enables-after-key-save and whisper.cpp path tests
- [x] 5.5 Add the broken-file state (red status line, controls disabled, API key still editable) and the 2 s reload. Verify with tests that break the file while the window is open and fix it again

## 6. GTK window

- [x] 6.1 Build `vox/ui/gtk/settings.py`: an `Adw.ViewStack` with the five pages, reusing the vocabulary pages; open on `--page`. Verify in `tests/test_settings_window.py` (Linux, Xvfb)
- [x] 6.2 Build the General page with `Adw.ComboRow`, `Adw.SwitchRow` and a scale saved on release, and Refresh with a spinner. Verify with tests that drive each row and read `config.toml`
- [x] 6.3 Turn the hotkey window into the Hotkey page with the same save, clash, page-switch and take-every-key rules. Verify by moving the GTK tests from `test_hotkey_window.py`
- [x] 6.4 Build the Transcription page, with `Gtk.FileDialog` for whisper.cpp and an `Adw.Dialog` for key entry. Verify by moving the GTK tests from `test_key_window.py` and adding the new cases
- [x] 6.5 Add the broken-file banner and the 2 s reload. Verify with the same break-and-fix tests as 5.5

## 7. Docs

- [x] 7.1 Update the README (tiny, with screenshots made from made-up data only), `docs/settings.md` (the window first, the file second, and which settings stay file-only), `docs/privacy.md` (where the screen hints switch is), `config.example.toml` and the CHANGELOG (Unreleased: Settings window, removed submenus, the microphone choice is now kept). Verify with `test_docs.py`
- [x] 7.2 Take new screenshots of each Settings page on macOS and Linux (Linux under Xvfb). Verify that the image files referenced by the README and docs exist

## 8. Specs

- [x] 8.1 Keep the delta specs in step with any behaviour that changed during implementation. Verify with `openspec validate settings-window --strict`

## 9. Verification

- [x] 9.1 `uv run --frozen pytest -q -p no:cacheprovider` and `uv run --frozen ruff check vox tests` pass on macOS
- [x] 9.2 The Linux suite passes under Xvfb on the Linux PC, with `VOX_REQUIRE_GTK=1` for the GTK window tests
- [x] 9.3 Install on both machines from an up-to-date checkout (check `origin/main` first). On each, the tray shows the new menu and `Settings…` opens
- [x] 9.4 A person tries every page once on a real display on macOS and on Linux: change the microphone, sounds, mode, language, hotkey, a word and a snippet; close Settings; check that the next dictation uses them
