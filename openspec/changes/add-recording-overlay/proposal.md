## Why

Vox Transfer currently gives desktop users little visual feedback while dictating or waiting for transcription. An optional compact overlay inspired by the personal website demo makes microphone activity and processing visible without interrupting the target application.

## What Changes

- Add a persisted “Show recording overlay” General setting, on by default, using the shared settings model and native settings controls.
- Deliver a native macOS and Linux/X11 floating panel near the bottom center of the target app's display, starting at approximately 224 × 44 logical pixels, with a prominent Vox icon, charcoal background, rounded border, layered waveform in brass for cloud or silver for local, and short status centered underneath the waveform.
- Drive “Listening” and smooth waveform amplitude from actual recording and microphone levels. Show a horizontal line with a looping glowing bead and “Transcribing…” while processing, or “Transcribing locally…” with silver for whisper.cpp.
- Hide before pasting text, fade on empty/no-speech completion, dismiss immediately on cancellation, and clear stale state on errors, disable, and quit. Isolate updates by dictation generation.
- Add a small top-right cancel button for recording and transcription, with double-tap cancellation opt-in through `[hotkey] double_tap_cancel = true` (default false).
- Preserve focus, cursor, keyboard shortcuts, clipboard restoration, paste destination, and screenshot/OCR context. Keep level sampling and UI work bounded and optional.
- Respect reduced motion and stop animation while hidden. Support Linux/X11 through the tray’s existing GTK 3 loop; preserve unsupported/headless dictation with a no-op backend and clear platform documentation.
- Verify persistence, lifecycle races, resource cleanup, and dictation isolation through automated tests and available macOS and Linux/X11 desktop checks.

## Capabilities

### New Capabilities

- `recording-overlay`: Optional desktop feedback panel, actual audio/state integration, placement, focus and capture isolation, motion accessibility, cleanup, and platform fallback.

### Modified Capabilities

- `desktop-frontend`: General Settings gains a persisted recording-overlay preference and explicit availability information on unsupported platforms.

## Impact

Touches `vox/config.py`, `vox/ui/settings_model.py`, native macOS and GTK settings, `vox/audio.py`, `vox/daemon.py`, main-thread dispatch in `vox/ui/tray.py`, and overlay controller, AppKit and GTK 3 rendering modules. Window geometry and OCR integration in `vox/window.py` may need small additions for placement and capture isolation. Reuses the existing native macOS stack and packaged Vox icon; no additional microphone stream; reuse GTK 3 with Cairo on Linux and leave headless operation free of GUI imports. Updates tests, README, and settings documentation. Coordinate daemon and settings edits with the existing `retry-failed-transcriptions` change; history retry UI remains outside this feature.
