The rename is on branch `vt/rename`, which starts from main at ae10a09, and its review fixes are on `vt/final`. Every test named here exists on that branch. Task 3.5 is left open: it needs a real Mac session, which was not used for this change.

## 1. Inventory

- [x] 1.1 List every "Vox" in `vox/`, `install.sh`, `packaging/`, `.github/`, the README, CHANGELOG, RELEASING.md and `config.example.toml`, and sort each into user-visible (renamed) or internal (kept, per the owner decision). Confirm that nothing parses `vox --help`, `vox --version` or log lines: `release.yml` and `packaging/deb/smoke-test.sh` only `grep -F` the version number, and the installer's smoke test only checks the exit status of `vox --help`. Verified by the final grep in task 6.1

## 2. What the app shows

- [x] 2.1 Make the tray status line begin "Vox Transfer ·" and rename the Quit item "Quit Vox Transfer"; verified by `test_initial_icon_is_idle_template`, `test_status_line_reports_the_most_urgent_problem_while_idle`, `test_missing_key_is_the_status_and_the_first_item`, `test_a_long_problem_is_shortened_to_one_line`, `test_recent_dictations_are_their_own_menu_section`, `test_quit_cancels_daemon_main_task` and `test_menu_to_daemon_to_icon_round_trip`
- [x] 2.2 Rename the app in the API key and vocabulary windows' text, shared by macOS and GTK; verified by `test_storage_text_names_the_keychain_or_warns`, `test_unchecked_result_asks_to_save_anyway`, `test_mac_unchecked_key_can_be_saved_anyway` and `test_gtk_unchecked_key_can_be_saved_anyway` (GTK runs on Linux only)
- [x] 2.3 Rename the app menu of the macOS windows ("Vox Transfer", "Quit Vox Transfer") and the GTK application name. No test reads them, so they are verified by the grep in task 6.1
- [x] 2.4 Rename the app in log lines, error and dependency messages; verified by `test_a_settings_file_that_loads_again_is_applied_and_lets_vox_record`, `test_a_broken_settings_file_that_is_deleted_lets_vox_record_on_the_defaults` and `test_missing_keyboard_backend_is_a_clear_dependency_error`
- [x] 2.5 Name Vox Transfer in the `vox --help` description, and make `vox --version` print `Vox Transfer X.Y.Z`; verified by `test_version_flag_prints_the_package_version`

## 3. macOS launcher and LaunchAgent

- [x] 3.1 Add the launcher as `Vox Transfer.app`, with `CFBundleName` and `CFBundleDisplayName` "Vox Transfer" and the same placement rules and ownership guard; verified by `test_mac_launcher_updates_its_own_launcher_in_place`, `test_mac_launcher_never_writes_into_another_app_named_vox_transfer`, `test_mac_launcher_is_skipped_when_both_places_have_another_app_named_vox_transfer` and `test_mac_launcher_goes_to_the_users_applications_when_the_shared_folder_is_read_only`
- [x] 3.2 After adding it, delete this user's own `Vox.app` launcher in `/Applications` or `~/Applications`, and never another app at that path; keep it when neither place is free for the new launcher; verified by `test_mac_launcher_replaces_the_vox_launcher_from_before_the_rename` (for both folders), `test_mac_launcher_never_removes_another_app_named_vox` and `test_mac_launcher_is_skipped_when_both_places_have_another_app_named_vox_transfer`
- [x] 3.3 Make `--uninstall` remove the user's own launcher under either name; verified by `test_mac_uninstall_removes_only_the_vox_launchers` and `test_mac_uninstall_carries_on_when_the_launcher_cannot_be_deleted`
- [x] 3.4 Name the launcher's bundle id in the LaunchAgent's `AssociatedBundleIdentifiers`; verified by `test_launch_agent_has_absolute_paths_and_private_logs`
- [ ] 3.5 On a spare Mac account, install from this branch over an install that has `Vox.app`, then check that Finder, Spotlight and System Settings > General > Login Items show Vox Transfer and that `Vox.app` is gone

## 4. Linux, the package and the release

- [x] 4.1 Set `Name=Vox Transfer` in the application and autostart entries that `install.sh` writes; verified by `test_linux_launchers_add_an_autostart_entry`
- [x] 4.2 Name Vox Transfer in the `.deb`'s desktop entries, the systemd unit's `Description=`, the package description and `Upstream-Name`, and the GitHub release title, while the package and unit stay `vox`; verified by `test_users_see_the_name_vox_transfer_while_the_package_and_service_stay_vox`
- [x] 4.3 Rename the app in installer messages; verified by `test_failed_build_keeps_the_previous_venv`, `test_mac_launcher_is_skipped_when_both_places_have_another_app_named_vox_transfer`, `test_mac_uninstall_carries_on_when_the_launcher_cannot_be_deleted` and `test_bootstrap_needs_a_published_release`
- [x] 4.4 Run the Linux suite, with the GTK window tests, under Xvfb on the Linux test PC; run on `vt/hotkey` and again on `vt/final`, which hold every rename commit: on `vt/final`, 945 passed and 77 skipped without GTK, and 37 GTK tests passed

## 5. Docs and specs

- [x] 5.1 Call the app Vox Transfer in the README (noting near Install that the command is still `vox`), CHANGELOG, RELEASING.md and `config.example.toml`, keeping commands, paths, units and package names; verified by `test_readme_documents_every_setting_at_its_default`, `test_readme_troubleshooting_gives_the_full_command_path`, `test_example_config_changes_nothing_as_shipped`, `test_example_config_shows_the_real_defaults`, `test_changelog_describes_this_version` and `test_relock_runs_the_pinned_uv_from_a_private_directory`
- [x] 5.2 Write the delta specs for `release-packaging`, `desktop-frontend`, `dictation-pipeline` and `macos-support`, and sync them into `openspec/specs`; verified by `openspec validate --all --strict`. A trial archive in a scratch copy found the synced specs already matching ("Specs already in sync"), so archiving this change later leaves them as they are

## 6. Verification

- [x] 6.1 Grep `vox/`, `install.sh`, `packaging/`, `.github/`, the README, CHANGELOG, RELEASING.md and `config.example.toml` for "Vox" not followed by " Transfer". Every match is a path (`~/Library/Logs/Vox`), the old launcher `Vox.app` (or the `Vox` executable and `Vox.icns` inside the launcher), an internal identifier (`VoxError`, `VoxIcon`, `VoxHistory`, `VoxClearHistory`), or a code comment or docstring that uses Vox as the program's short name
- [x] 6.2 `uv run --frozen pytest -q -p no:cacheprovider` passes on macOS (959 passed, 30 skipped; 955 before this change), and so do `uv run --frozen ruff check vox tests` and shellcheck of the scripts CI checks
