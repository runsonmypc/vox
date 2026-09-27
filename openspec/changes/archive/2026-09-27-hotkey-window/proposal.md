## Why

The owner wants users to choose their hotkey from the menu before the first public release, 1.0.0. Until now the hotkey could only be changed by editing `[hotkey]` in `config.toml`, and an edit took effect only after quitting and reopening the app, which a user had no way to learn from the app itself.

## What Changes

- **Menu**: a new item, "Set Hotkey…", sits right after "Set API Key…" in the windows section (or after "Vocabulary & Snippets…" while "Set API Key…" is at the top because the key is missing). It is enabled only while Vox is idle, not while it records or processes.
- **Window** (AppKit on macOS, GTK 4 with libadwaita on Linux, text shared through `vox/ui/hotkey_model.py`): two fields that record keys after they are clicked.
  - **Hotkey**: the key tapped on its own, saved as `[hotkey] key`. It accepts the eight modifiers on either side (Shift, Control, Option or Alt, Command or Super), AltGr on Linux, and F1 to F20. Typing keys are refused with a message, and the field keeps recording so the user can press another key at once.
  - **Key combination**: optional, saved as `[hotkey] fallback`. It needs one or more of Control, Option or Alt, Command or Super (Shift may be added) and ends with Space or F1 to F20. It is saved as listener names in a fixed order, such as `ctrl+space` or `right_ctrl+shift+f5`. A clear button removes it.
  - **Use Default** goes back to right Shift with no combination. **Cancel** closes without saving. **Save** writes `[hotkey]` and keeps the rest of `config.toml` and its comments. It writes nothing when nothing changed, so a user without a `config.toml` does not get one.
  - A combination that includes the hotkey cannot be saved, since the listener would never see it. Orange warnings, which still allow saving, cover F1 to F12 (and, on Linux, left Super and Alt).
  - Esc or clicking elsewhere stops recording and keeps the old value. While a field records, the window takes every key, so Return does not save and Space does not press a button.
  - When `config.toml` does not load, the window says so and saves nothing, like the vocabulary window.
- **No dictation while the window is open**: the tray sends the daemon `hotkey:suspend` before the window starts and `hotkey:resume` when its process ends, however it ends, or at once when it fails to start. While suspended, the daemon ignores toggle and cancel, with no busy sound. A recording that started just as the window opened is discarded.
- **Live apply**: on `hotkey:resume` the daemon re-reads `[hotkey]` and, when it changed, replaces the pynput listener with a new one. The config reloader does the same for hand edits within a few seconds. `[hotkey]` edits no longer wait for a restart, and the "take effect when Vox Transfer restarts" warning is gone.
- `resolve_key` and `parse_combo` become public functions of `vox/hotkey.py`, so the window names keys the way the listener does. On X11, the listener and the window name an Alt key pressed or released with Shift held (reported as Meta) as Alt, so a combination holding Alt and Shift fires in either order, and an Alt let go after Shift never stays counted as held. `vox/config.py` gets `update_hotkey`, built on the existing atomic writer.
- The README, CHANGELOG (1.0.0) and `config.example.toml` describe the window and the live apply.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `desktop-frontend`: a new requirement, Hotkey Selection from Tray, for the menu item and the window.
- `dictation-pipeline`: Settings Reload and Configuration File Errors now apply `[hotkey]` without a restart, and Hotkey Names names Alt as Alt while Shift is held on Linux.

## Impact

- Code: `vox/hotkey.py` (public `resolve_key` and `parse_combo`, and Alt named as Alt while Shift is held), `vox/config.py` (`update_hotkey`), `vox/daemon.py` (the suspend flag, `_suspend_hotkey`, `_reload_hotkey`, `_apply_hotkey`, and the reloader's `apply_hotkey` parameter), `vox/ui/tray.py` (the menu item, `_open_hotkey`, and `on_exit` after a failed launch), and the new `vox/ui/hotkey_model.py`, `vox/ui/hotkey_window.py`, `vox/ui/mac/hotkey.py` and `vox/ui/gtk/hotkey.py`.
- Events: `hotkey:suspend` and `hotkey:resume`, from the tray to the daemon.
- Settings: none new. The window writes the existing `[hotkey] key` and `fallback`. The default hotkey stays `right_shift`, and no default changes.
- Dependencies: none new.
- Tests: `tests/test_hotkey_window.py` is new; `test_tray.py`, `test_pipeline_daemon.py`, `test_config_mutation.py`, `conftest.py` and `test_isolation.py` gain the cases in tasks.md.
