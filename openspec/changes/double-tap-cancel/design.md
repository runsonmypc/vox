## Context

Currently, `HotkeyListener` tracks solo modifier and key combinations, emitting a `"toggle"` event to the daemon's asyncio queue on release. The daemon maintains a three-state machine (`IDLE`, `RECORDING`, `PROCESSING`). Single taps transition from `IDLE` to `RECORDING` and from `RECORDING` to `PROCESSING`. During `PROCESSING`, toggle events are ignored and play a `"busy"` sound. There is no cancellation mechanism; audio is always processed and pasted into the focused application unless speech detection flags it as empty.

See `proposal.md` for motivation and `specs/double-tap-cancel/spec.md` for behavioral requirements.

## Goals / Non-Goals

**Goals:**
- Detect consecutive taps of the configured hotkey within a configurable threshold (`double_tap_timeout_ms`, default 400ms) as a `"cancel"` event.
- Support cancellation during `RECORDING` state: instantly stop audio capture, discard frames, cancel streaming workers/OCR, restore attenuated volume, and reset state to `IDLE`.
- Support cancellation during `PROCESSING` state: cancel in-flight batch/streaming transcription tasks, close WebSocket connections, suppress clipboard manipulation and paste injection, restore volume, and reset state to `IDLE`.
- Provide zero latency penalty on starting recording (no deferred tap timer before initiating capture).
- Play distinct auditory feedback (`"cancel"` sound) upon cancellation.

**Non-Goals:**
- Post-injection undo (reverting text already pasted into an application).
- Multi-tap gestures beyond double-tap (e.g., triple-tap).

## Decisions

### Decision 1: Immediate First Tap Dispatch with Double-Tap Follow-Up
- **Choice**: Emit `"toggle"` immediately on the first key release. If a second release occurs within `double_tap_timeout_ms`, emit `"cancel"` and reset the double-tap tracking window.
- **Rationale**: Deferring the first tap by 400ms to verify whether a second tap arrives would introduce intolerable delay when starting recording (users expect instantaneous audio capture the moment the hotkey is released or pressed). By emitting `"toggle"` immediately, start and stop recording remain instant. If the user rapidly taps a second time, the state machine transitions to cancel and cleanly discards the operation before transcription completes or pastes.
- **Alternatives considered**:
  - *Deferred single-tap detection*: Wait 400ms before sending `"toggle"`. Rejected because 400ms latency on start recording clips initial speech and feels unresponsive.

### Decision 2: Double-Tap State Tracking in `HotkeyListener`
- **Choice**: Maintain `_last_release_time` in `HotkeyListener`. On key release of the configured hotkey:
  - If `(now - self._last_release_time) * 1000 <= self._double_tap_timeout_ms` and no other key was pressed:
    - Emit `"cancel"` to the queue
    - Reset `_last_release_time = 0.0` (preventing a third tap from being counted as another double tap)
  - Else:
    - Emit `"toggle"` to the queue
    - Update `_last_release_time = now`
  - If any other key is pressed between taps, reset `_last_release_time = 0.0`.
- **Rationale**: Isolates keyboard timing, debounce logic, and key sequence validation to the listener component, delivering clean, explicit event signals (`"toggle"` vs `"cancel"`) to the daemon queue.

### Decision 3: State Machine Cancellation Handling in `daemon._main`
- **Choice**: Extend daemon event loop to handle `"cancel"`:
  - In `State.RECORDING`:
    - Stop recorder and discard audio frames
    - Cancel `screen_capture_future` if pending
    - Cancel `stream_task` and close `streaming_transcriber` if active
    - Restore saved volume if attenuation was active
    - Play `"cancel"` sound
    - Transition to `State.IDLE`
  - In `State.PROCESSING`:
    - Cancel `process_task` (raising `asyncio.CancelledError` inside `_process` before paste)
    - Cancel `stream_task` and close `streaming_transcriber` if still running
    - Restore saved volume if attenuation was active
    - Play `"cancel"` sound
    - Transition to `State.IDLE`
  - In `State.IDLE`: Ignore `"cancel"` event.
- **Rationale**: Ensures resources (sound streams, audio buffers, network WebSockets, system volume) are completely cleaned up without leaving orphan tasks or corrupted clipboard states.

### Decision 4: Cancellation Sound Feedback
- **Choice**: Add a `"cancel"` entry in `SoundPlayer`:
  - macOS Darwin: Map to system alert sound `"Blow"` (or `"Purr"` if unavailable) for a distinctive, crisp discard tone.
  - Linux / synthetic: Descending two-tone sweep (900 Hz for 60ms, then 450 Hz for 80ms).
- **Rationale**: Distinct from `"stop"` (Pop) and `"error"` (Basso/low buzz), giving clear confirmation that the dictation was aborted intentionally rather than failing with an error.

## Risks / Trade-offs

- **[Risk]** Accidental double-tap when stopping recording triggers a cancel instead of processing.
  - **Mitigation**: A 400ms window matches standard OS double-click thresholds. Normal stop usage involves a single tap. The window can be configured via `[hotkey] double_tap_timeout_ms` in `config.toml`.
- **[Risk]** Cancellation race condition with fast transcription completion.
  - **Mitigation**: If `_process` finishes before the second tap arrives, text may already be pasted. However, typical Whisper batch transcription takes 800ms-2500ms and streaming finalization takes 50-200ms. If cancelled while `_process` is awaiting, `process_task.cancel()` aborts before reaching `paste(...)`.
