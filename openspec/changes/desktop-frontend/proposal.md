## Why

Currently, `vox` runs as an invisible command-line daemon with no visual feedback on recording status, no way to switch microphones without restarting the process or editing TOML files, no safety net to recover or re-copy past dictations if an active application loses focus, and no simple way to manage custom vocabulary or snippets. Adding an ultra-lightweight, cross-platform system tray and quick-access history/settings UI gives users essential desktop management without introducing heavy webview dependencies or screen-obstructing HUD overlays.

## What Changes

- Add a cross-platform system tray / menu bar integration via `pystray` with dynamic icon states (Idle vs. Recording) for both macOS and Linux.
- Add tray dropdown controls to switch audio input devices on the fly, toggle Vox pause/mute, and quick-copy the last 3 dictations.
- Implement an automatic local SQLite history logger (`~/.local/share/vox/history.db`) that records past dictations with timestamps, app context, and durations.
- Add a lightweight popover/drawer window for searching past dictation history with 1-click clipboard copy and re-paste.
- Add a visual manager for custom vocabulary (`config.dictionary`) and text expansion snippets (`config.snippets`) that updates and hot-reloads `config.toml`.

## Capabilities

### New Capabilities
- `desktop-frontend`: System tray status integration, microphone device selector, local SQLite dictation history recovery drawer, and visual dictionary/snippet manager.

### Modified Capabilities
<!-- None: existing streaming and platform capabilities remain unchanged. -->

## Impact

- Added dependencies: `pystray`, `Pillow` (for dynamic tray icons), and a lightweight GUI framework (e.g. `slint` or native OS popover).
- Code paths affected: `vox.daemon` (lifecycle hooks, state broadcast, history persistence), `vox.config` (programmatic updates to dictionary and snippets), and new frontend package `vox.ui`.
- Storage impact: Creates local SQLite database at `~/.local/share/vox/history.db`.
