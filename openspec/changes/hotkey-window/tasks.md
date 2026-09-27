The change is on branch `vt/hotkey`, which starts from `vt/rename` (6cf8436), and its review fixes are on `vt/final`. Every test named here exists on `vt/final`. Task 9.4 is left open: it needs a person at a real display, which agents must not use.

## 1. Listener and config

- [x] 1.1 Make `resolve_key` and `parse_combo` public functions of `vox/hotkey.py`, with the same bodies, and use them in `HotkeyListener`; verified by `test_hotkey_spellings_match_the_key_pynput_reports`, `test_hotkey_fallback_combo_accepts_left_modifier_names` and `test_recorded_names_read_back_unchanged`
- [x] 1.3 Name `Meta_L` and `Meta_R`, which X11 reports for the Alt keys while Shift is held, `alt` and `right_alt` in the listener and in `LINUX_KEYS`; verified by `test_alt_with_shift_held_is_still_alt`, `test_linux_key_names_name_the_keys_the_listener_reports` (Linux) and `test_gtk_records_alt_pressed_after_shift` (Linux, under Xvfb)
- [x] 1.2 Add `update_hotkey(path, key, fallback)` to `vox/config.py`: set `key`, set `fallback` or remove it when empty, refuse an empty key, and write through the existing atomic writer; verified by `test_hotkey_is_saved_in_the_hotkey_section`, `test_clearing_the_combination_removes_fallback`, `test_hotkey_update_creates_a_private_file` and `test_a_section_that_is_not_a_table_is_a_config_error_not_a_crash`

## 2. Daemon

- [x] 2.1 Handle `hotkey:suspend` and `hotkey:resume`: while suspended, ignore toggle and cancel with no sound, and cancel a recording that is running when the suspend arrives; verified by `test_the_hotkey_does_nothing_while_the_hotkey_window_is_open` and `test_opening_the_hotkey_window_during_a_recording_discards_it`
- [x] 2.2 On resume, re-read `[hotkey]` and replace the listener only when key, fallback or `double_tap_timeout_ms` changed; keep the listener when the file does not load; verified by `test_a_hotkey_saved_in_the_window_applies_when_it_closes`, `test_closing_the_hotkey_window_without_a_change_keeps_the_listener` and `test_closing_the_hotkey_window_with_a_broken_settings_file_keeps_the_listener`
- [x] 2.3 Give `_config_reloader` an `apply_hotkey` callback that gets every version of the file that loads, start it with `_apply_hotkey`, and remove the "take effect when Vox Transfer restarts" warning; verified by `test_a_hand_edited_hotkey_applies_without_a_restart` and `test_a_settings_file_that_loads_again_is_applied_and_lets_vox_record`

## 3. Tray

- [x] 3.1 Add "Set Hotkey…" after the windows-section "Set API Key…" slot, enabled only while idle; verified by `test_set_hotkey_follows_set_api_key`, `test_set_hotkey_is_unavailable_while_recording_or_processing` and `test_recent_dictations_are_their_own_menu_section`
- [x] 3.2 Send `hotkey:suspend` before launching `vox.ui.hotkey_window --config PATH`, and `hotkey:resume` when its process exits; when a window fails to launch, call its exit callback at once; verified by `test_hotkey_window_suspends_the_hotkey_until_it_closes` and `test_a_hotkey_window_that_fails_to_open_resumes_the_hotkey`

## 4. Model

- [x] 4.1 Map macOS key codes and GTK key names to the listener's names, and label them per platform; verified by `test_mac_key_codes_name_the_keys_the_listener_reports` (macOS), `test_linux_key_names_name_the_keys_the_listener_reports` (Linux) and `test_reload_shows_the_saved_hotkey_in_any_spelling`
- [x] 4.2 Add `Capture`: a key that is not a modifier completes the keys at once, releasing a modifier completes the modifiers pressed, and a modifier held before recording is ignored; verified by `test_a_modifier_tapped_alone_is_recorded_when_released`, `test_a_combination_is_recorded_when_its_last_key_is_pressed`, `test_modifiers_alone_are_recorded_together`, `test_a_key_held_before_recording_is_ignored` and `test_the_combination_field_shows_the_held_modifiers`
- [x] 4.3 Accept only modifiers, AltGr on Linux and F1 to F20 as the hotkey, and a modifier other than Shift ending with Space or F1 to F20 as a combination, saved in a fixed order; verified by `test_modifiers_and_function_keys_can_be_the_hotkey`, `test_typing_keys_cannot_be_the_hotkey`, `test_a_combination_pressed_for_the_hotkey_points_to_its_own_field`, `test_a_combination_needs_a_modifier_and_space_or_a_function_key` and `test_a_combination_is_saved_in_a_fixed_order`
- [x] 4.4 Refuse to save a combination that includes the hotkey, warn about F1 to F12 (and on Linux left Super and Alt), and order the status line: a refusal or the hint while recording, then the clash, then a warning; verified by `test_the_combination_may_not_include_the_hotkey` and `test_function_keys_and_linux_tap_keys_warn`
- [x] 4.5 Save only a change, keep an untouched spelling as written, go back to the default on Use Default, and never write a file that does not load; verified by `test_saving_writes_the_hotkey_section_and_keeps_the_rest`, `test_saving_without_a_change_writes_nothing`, `test_use_default_goes_back_to_right_shift_without_a_combination` and `test_a_broken_settings_file_is_never_written`
- [x] 4.6 Add `vox/ui/hotkey_window.py` (`vox-hotkey --config PATH`), whose model reads nothing until the window is built; verified by `test_main_leaves_reading_the_settings_to_the_window`

## 5. macOS window

- [x] 5.1 Build `HotkeyController`: the two push-on-push-off fields, the clear button, Use Default, Cancel (Esc) and Save (Return), the status line, and a local key monitor that tells left and right modifiers apart by their device bits; verified by `test_mac_records_a_modifier_tapped_on_its_own` and `test_mac_records_a_combination_and_offers_to_clear_it`
- [x] 5.2 While a field records, take every key: refuse a typing key and keep recording, stop on Esc, and ignore fn; otherwise let keys through, and drop repeated key presses; verified by `test_mac_refuses_a_typing_key_and_keeps_recording`, `test_mac_escape_stops_recording_and_keeps_the_key`, `test_mac_ignores_fn_so_function_keys_can_be_recorded_with_it` and `test_mac_keys_pass_through_while_not_recording`
- [x] 5.3 Save and close, stay open on a clash, warn about F1 to F12, and disable recording when the file does not load; verified by `test_mac_save_writes_the_settings_and_closes`, `test_mac_clash_keeps_the_window_open`, `test_mac_warns_about_f1_to_f12` and `test_mac_broken_settings_file_disables_recording`

## 6. GTK window

- [x] 6.1 Build `HotkeyWindow`: a header bar with Cancel and Save, a preferences group with Use Default, the two rows with their field buttons and the clear button, the status line and a banner, and a key controller in the capture phase; verified by `test_gtk_records_a_modifier_tapped_on_its_own`, `test_gtk_records_altgr_as_the_listener_names_it` and `test_gtk_records_a_combination_and_offers_to_clear_it` (Linux, under Xvfb)
- [x] 6.2 While a field records, take every key: refuse a typing key and keep recording, and stop on Escape; drop a key held down after it was recorded; verified by `test_gtk_refuses_a_typing_key_and_keeps_recording`, `test_gtk_escape_stops_recording_and_keeps_the_key` and `test_gtk_swallows_a_held_key_after_recording_it` (Linux, under Xvfb)
- [x] 6.3 Save and close, and show a banner and disable recording when the file does not load; verified by `test_gtk_save_writes_the_settings_and_closes` and `test_gtk_broken_settings_file_shows_a_banner_and_disables_recording` (Linux, under Xvfb)

## 7. Tests' isolation

- [x] 7.1 Point `vox.ui.hotkey_window`'s `DEFAULT_CONFIG_PATH` at each test's own file; verified by `test_default_config_path_is_per_test`

## 8. Docs and specs

- [x] 8.1 Describe Set Hotkey… and the live apply of `[hotkey]` in the README, the CHANGELOG's 1.0.0 section and `config.example.toml`, and drop the restart note; verified by `test_readme_documents_every_setting_at_its_default`, `test_example_config_changes_nothing_as_shipped`, `test_example_config_shows_the_real_defaults` and `test_changelog_describes_this_version`
- [x] 8.2 Write the delta specs for `desktop-frontend` (a new Hotkey Selection from Tray requirement) and `dictation-pipeline` (Settings Reload and Configuration File Errors), and sync them into `openspec/specs`; verified by `openspec validate --all --strict`, and by a trial archive in a scratch copy that found the synced specs already matching ("Specs already in sync"), so archiving this change later leaves them as they are

## 9. Verification

- [x] 9.1 `uv run --frozen pytest -q -p no:cacheprovider` passes on macOS (1019 passed, 40 skipped on `vt/final`; 959 passed and 30 skipped before this change), and so does `uv run --frozen ruff check vox tests`
- [x] 9.2 The Linux suite passes under Xvfb on the Linux test PC, with the GTK window tests and the Linux key-name test: on `vt/final`, 945 passed and 77 skipped without GTK, and 37 GTK tests passed with `VOX_REQUIRE_GTK=1`; `test_linux_key_names_name_the_keys_the_listener_reports`, `test_alt_with_shift_held_is_still_alt` and `test_gtk_records_alt_pressed_after_shift` ran there and passed
- [x] 9.3 `openspec validate hotkey-window --strict` passes
- [ ] 9.4 A person tries the window once on a real display on macOS and on Linux: records Right Command, then Control + Space, saves, and checks that the new keys dictate at once and the old key does not
