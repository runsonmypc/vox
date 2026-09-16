## Why

Currently, vox provides a single toggle mechanism via the configured hotkey (default `right_shift`): a press starts recording, and a subsequent press stops recording and immediately begins transcription and text injection. If a user accidentally starts dictation, coughs, makes a verbal error, or decides they do not want to insert text, there is no way to abort. Once recording finishes, transcription runs to completion and injects the text into the active application.

Introducing a double-tap cancellation mechanism allows users to quickly abort active recordings and in-flight transcriptions, cleanly discarding the audio without pasting any text into the active window.

## What Changes

- **Double-Tap Hotkey Detection**: Detect two rapid taps of the configured hotkey (or fallback) within a configurable threshold window (default 400ms).
- **Cancel During Recording**: If double-tapped while in the `RECORDING` state, abort audio recording immediately, cancel any live streaming workers, discard recorded audio chunks, restore system volume attenuation, and reset daemon state to `IDLE` without transcribing.
- **Cancel During Processing**: If double-tapped while in the `PROCESSING` state, abort active batch transcription or streaming finalization tasks, discard any received transcript, suppress clipboard manipulation and paste key simulation, restore volume attenuation if still pending, and reset daemon state to `IDLE`.
- **Cancellation Audio Feedback**: Introduce a dedicated "cancel" sound indicator (e.g. macOS system alert sound like `Sosumi` or synthetic two-tone descending cancel tone) to give immediate auditory confirmation that the turn was discarded.
- **Configurable Cancellation Settings**: Add optional configuration options under `[hotkey]` for `double_tap_timeout_ms` (default 400ms).

## Capabilities

### New Capabilities
- `double-tap-cancel`: Defines double-tap hotkey detection for discarding ongoing audio recordings and in-flight transcriptions, resetting state machine to idle, restoring attenuated volume, and playing cancellation audio feedback.

### Modified Capabilities
<!-- None. Existing requirements in macos-support and streaming-transcription remain valid; cancellation behavior is specified in the double-tap-cancel capability. -->

## Impact

- **Affected Code**:
  - `vox/hotkey.py`: Track rapid consecutive hotkey presses/releases to distinguish double-taps, or propagate double-tap event notifications to the daemon queue.
  - `vox/daemon.py`: State machine handling for `cancel` event in both `RECORDING` and `PROCESSING` states, cancelling asyncio tasks, discarding audio, and restoring volume.
  - `vox/sounds.py`: New `cancel` sound registration and fallback synthetic tone.
  - `vox/config.py`: Optional configuration for `double_tap_timeout_ms`.
  - `tests/test_daemon.py`, `tests/test_hotkey.py`, `tests/test_sounds.py`: Unit tests for double-tap detection, cancellation state transitions, audio discard, and cleanup.
- **Dependencies**: No external library additions; uses existing `pynput`, `asyncio`, and audio playback facilities.
