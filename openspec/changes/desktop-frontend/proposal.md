## Why

Currently, `vox` runs as an invisible command-line daemon with no visual feedback on recording status, no way to switch microphones without restarting the process or editing TOML files, no safety net to recover or re-copy past dictations if an active application loses focus, and no simple way to manage custom vocabulary or snippets. Adding an ultra-lightweight, cross-platform system tray and quick-access history/settings UI gives users essential desktop management without introducing heavy webview dependencies or screen-obstructing HUD overlays.

## What Changes

- Add a cross-platform system tray via `pystray` with dynamic icon states (Idle vs. Recording): the macOS menu bar, and the Linux AppIndicator / StatusNotifierItem tray.
- Add tray dropdown controls to switch audio input devices on the fly, toggle Vox pause/mute, and quick-copy the last 3 dictations.
- Add a transcription submenu for OpenAI batch, OpenAI streaming, and local whisper.cpp, with persistent selection while idle.
- Implement an automatic local SQLite history logger (`~/.local/share/vox/history.db`) that records past dictations with timestamps, app context, and durations.
- Add a lightweight popover/drawer window for searching past dictation history with 1-click clipboard copy and re-paste.
- Add a visual manager for custom vocabulary (`config.dictionary`) and text expansion snippets (`config.snippets`) that updates and hot-reloads `config.toml`.
- Add `install.sh`, a per-user installer for macOS and Linux that sets up an isolated virtualenv, any missing Linux system packages, the GNOME AppIndicator extension, the API key, login autostart, and a Vox launcher for starting it again after Quit, so Vox runs without a source checkout or a compiler.

## Capabilities

### New Capabilities
- `desktop-frontend`: System tray status integration, microphone and transcription selectors, local SQLite dictation history recovery drawer, and visual dictionary/snippet manager.

### Modified Capabilities
<!-- None: existing streaming and platform capabilities remain unchanged. -->

## Impact

- Added dependencies: `pystray`, `Pillow` (for dynamic tray icons), and `tomlkit` (comment-preserving `config.toml` writes). Windows use native toolkits: AppKit through the existing PyObjC dependency on macOS, and GTK 4 with libadwaita on Linux. On Linux the tray and windows use the system `python3-gi` with the AppIndicator, GTK 4 and libadwaita typelibs, which `install.sh` installs when missing.
- `webrtcvad` (source-only, needs a compiler and pins `setuptools<81` for `pkg_resources`) is replaced by the drop-in `webrtcvad-wheels`, which ships prebuilt wheels.
- Code paths affected: `vox.daemon` (lifecycle hooks, state broadcast, history persistence), `vox.config` (programmatic updates to dictionary and snippets), and new frontend package `vox.ui`.
- Storage impact: Creates local SQLite database at `~/.local/share/vox/history.db`.
