## Context

See `proposal.md` for background and motivation.

`vox` is structured as a modular Python daemon (`pynput`, `sounddevice`, `numpy`, `openai`). Several submodules contain direct Linux/X11 system couplings:
- `vox/__main__.py`: Linux abstract Unix domain socket lock (`\0vox-daemon`).
- `vox/injector.py`: `xdotool`, `xclip`, and `python-xlib` (X11 XTest).
- `vox/window.py`: `xdotool`, `xprop WM_CLASS`, and `vox._atspi_reader` (Linux AT-SPI D-Bus).
- `vox/attenuation.py`: Linux PipeWire `wpctl`.
- `vox.service`: Systemd user service.

On macOS Darwin, these subsystems need native platform adapters while preserving Linux/X11 compatibility for Linux hosts (`pc-tail`).

## Goals / Non-Goals

**Goals:**
- Provide 100% feature parity on macOS: global hotkey toggle, sound feedback, mic recording, VAD filtering, active window detection, context-aware OpenAI transcription, text injection, audio attenuation, and background auto-start.
- Preserve zero-regression compatibility for Linux environments.
- Use native macOS APIs and built-in CLI tools (`osascript`, `screencapture`, `NSPasteboard`, Apple `Vision` framework) to minimize external third-party dependencies.
- Provide clear diagnostics and guidance for macOS TCC permissions (Accessibility, Microphone, Screen Recording).

**Non-Goals:**
- Supporting Windows.
- Building a standalone Swift menu-bar GUI app or status item (the focus is the existing headless daemon workflow).
- Rewriting the audio pipeline (recording and VAD are already platform-agnostic and work on macOS).

## Decisions

### Decision 1: Single-Instance Locking via `fcntl.flock`
- **Chosen**: Use an advisory file lock (`fcntl.flock`) on a lock file (`/tmp/vox-daemon.lock` or `~/.config/vox/vox.lock`).
- **Rationale**: Abstract Unix domain sockets (`\0...`) are a Linux-only kernel feature. `fcntl.flock` is standard POSIX, works identically on Linux and macOS Darwin, automatically releases locks upon process exit (including SIGKILL), and does not leave stale sockets on disk.
- **Alternatives Considered**:
  - *Localhost TCP port*: Can collide with other applications or be blocked by local firewall rules.
  - *Named Unix socket*: Requires cleanup logic and handling stale socket files after unclean shutdowns.

### Decision 2: Text Injection via In-Process Pasteboard and `Cmd+V`
- **Chosen**: On macOS, use `NSPasteboard` from `AppKit` (which is already installed as a transitive dependency of `pynput` on macOS) to back up, write, and restore clipboard contents in-memory. Simulate `Cmd+V` (`⌘V`) via `pynput.keyboard.Controller`.
- **Rationale**:
  - `NSPasteboard` operates in-process with sub-millisecond latency and zero subprocess overhead compared to spawning `pbcopy`/`pbpaste`.
  - On macOS, all applications—including GUI terminals like Ghostty, iTerm2, Terminal.app, and Alacritty—paste using `Cmd+V` (unlike Linux terminals which use `Ctrl+Shift+V`).
- **Alternatives Considered**:
  - *AppleScript keystroke*: `osascript -e 'tell application "System Events" to keystroke "v" using command down'` has ~100ms subprocess overhead and requires System Events assistive access. `pynput.keyboard.Controller` posts events directly via CoreGraphics `CGEvent`.

### Decision 3: Active Window Detection via Cocoa `NSWorkspace`
- **Chosen**: On macOS, query `NSWorkspace.sharedWorkspace().frontmostApplication()` to retrieve the active application's `localizedName()`, `bundleIdentifier()`, and `processIdentifier()`. For window title, query AppleScript with graceful fallback to application name.
- **Rationale**:
  - `NSWorkspace.frontmostApplication()` requires zero special permissions and executes instantly in-process.
  - App classification can match both bundle IDs (e.g., `com.mitchellh.ghostty`, `com.tinyspeck.slackmacgap`, `com.microsoft.VSCode`) and localized names against `AppType`.
- **Alternatives Considered**:
  - *`CGWindowListCopyWindowInfo`*: Window titles are redacted by macOS unless Screen Recording permission is explicitly granted.

### Decision 4: Neural Screen OCR via Apple Vision Framework
- **Chosen**: On macOS, capture the screen using `screencapture -x` to a temporary buffer and perform OCR using Apple's neural `Vision` framework (`VNRecognizeTextRequest`).
- **Rationale**:
  - Built directly into macOS, highly optimized on Apple Silicon neural engines (~50ms execution).
  - Eliminates the need to install external OCR packages (`tesseract`, `maim`) via Homebrew.
- **Alternatives Considered**:
  - *macOS Accessibility (AXUIElement)*: Deep accessibility tree walking can cause UI lag or freeze unresponsive apps. Vision OCR runs asynchronously in the existing background thread pool.

### Decision 5: System Volume Attenuation via AppleScript
- **Chosen**: Query volume via `osascript -e "output volume of (get volume settings)"` and set volume via `osascript -e "set volume output volume <val>"`.
- **Rationale**: Simple, zero extra C/Objective-C bindings, reliable across all macOS audio hardware.

### Decision 6: Startup Permission Diagnostics
- **Chosen**: Check `AXIsProcessTrusted()` from `ApplicationServices` during startup in `check_dependencies()`.
- **Rationale**:
  - If Accessibility is not enabled, global hotkeys and keystroke injection fail silently or produce phantom behavior.
  - Calling `AXIsProcessTrustedWithOptions` can trigger the native system prompt or log a helpful message instructing the user to enable Accessibility for their terminal or Python executable.

### Decision 7: Background Execution via LaunchAgent
- **Chosen**: Provide a LaunchAgent definition `com.runsonmypc.vox.plist` targeting `~/Library/LaunchAgents/`.
- **Rationale**: Standard macOS mechanism for user-session daemons, replacing systemd.

## Risks / Trade-offs

- **[Risk] macOS TCC Accessibility Permission**: The process running `vox` (or its terminal wrapper) must be granted Accessibility permissions.
  - *Mitigation*: Startup validation alerts the user immediately with actionable instructions instead of failing silently during hotkey registration.
- **[Risk] Screen Recording Permission for OCR**: Full screen capture may trigger a Screen Recording permission prompt on macOS Sonoma/Sequoia.
  - *Mitigation*: Screen OCR runs in a background thread and errors are caught gracefully; transcription continues normally using custom dictionary and app name if OCR is unavailable.
- **[Risk] Hotkey Conflicts**: `right_shift` solo press behavior may differ depending on macOS keyboard layouts.
  - *Mitigation*: Existing `hotkey_fallback = "ctrl+space"` is preserved and fully supported on macOS.

## Migration Plan

1. Implement platform conditional dispatch in `vox/injector.py`, `vox/window.py`, `vox/attenuation.py`, and `vox/__main__.py`.
2. Update `pyproject.toml` with Darwin-conditional dependencies (`pyobjc-framework-Vision`).
3. Update `start.sh` for POSIX symlink resolution.
4. Add `com.runsonmypc.vox.plist` and install instructions for macOS.
5. Verify on macOS by running tests, querying devices, and validating dry-run startup.
