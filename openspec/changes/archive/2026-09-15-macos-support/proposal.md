## Why

`vox` was originally built as a Linux-only voice-to-text daemon tailored to X11/GNOME desktop environments. Running it on macOS causes immediate failures due to Linux-specific assumptions: abstract Unix domain sockets, `xdotool`/`xclip`/`python-xlib` dependencies, `wpctl` PipeWire volume controls, AT-SPI accessibility D-Bus readers, and systemd service units.

Adding native macOS support enables seamless voice-to-text dictation on macOS Darwin (Apple Silicon and Intel) while preserving existing Linux/X11 compatibility.

## What Changes

- **Platform Abstraction**: Detect host platform (`sys.platform`) and branch runtime strategies cleanly between Linux and macOS.
- **Single-Instance Locking**: Replace Linux-only abstract Unix domain sockets (`\0vox-daemon`) with a cross-platform advisory file lock (`fcntl.flock`).
- **Text Injection**: Provide macOS native text injection using `NSPasteboard` (or `pbcopy`/`pbpaste`) and `Cmd+V` simulation via `pynput` / CoreGraphics `CGEvent`, replacing `xclip`, `xdotool`, and `Xlib.ext.xtest`.
- **Active Window & App Classification**: Query frontmost application via Cocoa `NSWorkspace` and window title via AppleScript on macOS, mapping macOS bundle IDs and process names to `AppType`.
- **Screen Context & OCR**: Capture screen context on macOS using native `screencapture` and Apple's built-in neural `Vision` framework (`VNRecognizeTextRequest`), replacing Linux AT-SPI and `maim` + `tesseract`.
- **Audio Attenuation**: Query and adjust macOS system volume during recording using AppleScript volume commands, replacing Linux `wpctl`.
- **System Permissions (TCC) Diagnostic**: Provide clear startup diagnostics for macOS Accessibility, Microphone, and Screen Recording permissions with actionable guidance.
- **Background Daemon Management**: Add a macOS LaunchAgent property list (`com.runsonmypc.vox.plist`) to replace systemd user services on macOS.
- **Portable Scripting**: Update `start.sh` to resolve symlinks portably across BSD/macOS and GNU Linux.

## Capabilities

### New Capabilities
- `macos-support`: Native macOS system integration including Cocoa active window detection, CoreGraphics/NSPasteboard text injection, Apple Vision screen OCR, AppleScript volume attenuation, cross-platform process locking, and TCC permission checks.

### Modified Capabilities
<!-- None: Initial OpenSpec specification in this repository -->

## Impact

- **Core Modules Modified**:
  - `vox/__main__.py`: Instance locking, platform-aware startup dependency and permission checks.
  - `vox/injector.py`: Platform-conditional clipboard operations and paste keystroke generation.
  - `vox/window.py`: Platform-conditional window inspection and screen context capture.
  - `vox/attenuation.py`: Platform-conditional volume query and adjustment.
  - `vox/config.py`: Default app mapping extensions for macOS bundle identifiers and applications.
  - `start.sh`: POSIX-compliant symlink resolution.
  - `pyproject.toml`: Platform-specific dependency specifications (`pyobjc-framework-Vision; sys_platform == 'darwin'`).
- **New Files**:
  - `com.runsonmypc.vox.plist`: LaunchAgent definition for macOS background execution.
- **External Dependencies**: No new external CLI tools required on macOS (uses built-in `screencapture`, `osascript`, `NSPasteboard`, and Apple `Vision` framework).
