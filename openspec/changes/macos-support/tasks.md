## 1. Dependencies and Single-Instance Lock

- [x] 1.1 Update `pyproject.toml` to specify platform-conditional dependencies (including `pyobjc-framework-vision; sys_platform == 'darwin'`) and verify with `uv pip install -e .`
- [x] 1.2 Replace abstract Unix socket lock in `vox/__main__.py` with `fcntl.flock` advisory file locking and verify mutual exclusion by attempting to run two concurrent instances
- [x] 1.3 Add macOS startup permission checks (`AXIsProcessTrusted`) in `vox/injector.py` and `vox/__main__.py`, verifying helpful warning messages when permissions are absent

## 2. macOS Text Injection and Clipboard

- [x] 2.1 Implement in-process clipboard backup, update, and restore using `NSPasteboard` on macOS in `vox/injector.py` while retaining `xclip` on Linux, and verify with a test clipboard round-trip
- [x] 2.2 Implement `Cmd+V` (`⌘V`) paste simulation using `pynput.keyboard.Controller` for all macOS applications in `vox/injector.py` and verify text injection

## 3. Active Window Detection and Screen Context

- [x] 3.1 Implement macOS frontmost application detection via Cocoa `NSWorkspace` and bundle identifier mapping to `AppType` in `vox/window.py`, verifying classification on active windows
- [x] 3.2 Add window title retrieval via AppleScript in `vox/window.py` with fallback to localized application name
- [x] 3.3 Implement macOS screen context capture using `screencapture -x` and Apple `Vision` framework neural OCR in `vox/window.py`, verifying vocabulary extraction into transcription prompts

## 4. Audio Attenuation and Service Files

- [x] 4.1 Implement macOS audio volume attenuation in `vox/attenuation.py` using AppleScript volume commands, verifying volume is lowered during recording and restored after
- [x] 4.2 Update `start.sh` to use portable POSIX path resolution and verify execution on macOS
- [x] 4.3 Create `com.runsonmypc.vox.plist` LaunchAgent property list file and verify with `plutil -lint`

## 5. End-to-End Verification

- [x] 5.1 Perform an end-to-end test on macOS (config loading, device query, hotkey listener start, and clean shutdown on SIGINT) to verify full system readiness
