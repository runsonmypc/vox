## Purpose

Provide optional desktop feedback for real microphone activity and transcription while preserving dictation focus, context privacy, and reliable text delivery.

## ADDED Requirements

### Requirement: Compact native panel and display placement
On a supported macOS or Linux/X11 desktop with the preference enabled, the system SHALL show a compact floating panel after microphone recording successfully starts. It SHALL use the website reference's charcoal background, subtle border, rounded corners, Vox microphone glyph directly on the panel without a separate square background, and layered waveform in brass for cloud or silver for local. Approximately 224 × 44 logical pixels SHALL be the starting layout, with the icon on the left, the waveform or processing line centered vertically in the panel, and an 8 px status label below the signal row, horizontally centered with the waveform or processing line. The gap from the visible microphone body at waveform height to the signal SHALL equal the gap from the signal to the panel’s inner rim: 40 clear pixels in the 2× native render (20 logical pixels). The microphone grille SHALL be red while recording and blue while transcribing, matching the menu bar/tray state colors. Placement SHALL be near the bottom center of the display containing the focused application window, within usable display bounds.

#### Scenario: Recording starts on a secondary display
- **WHEN** recording starts with the target window on a secondary display
- **THEN** the panel appears near that display's bottom center

#### Scenario: No target window geometry
- **WHEN** window geometry cannot identify a display
- **THEN** placement uses the active application's display when available and otherwise the primary display without failing dictation

#### Scenario: Disabled or failed start
- **WHEN** the preference is off, the display is unsupported, or the microphone cannot start
- **THEN** no listening panel appears and existing error feedback remains available

### Requirement: Actual microphone feedback with bounded work
During recording the panel SHALL say “Listening”. Waveform amplitude SHALL respond smoothly to measured microphone levels and settle toward a quiet line during silence. Listening MAY use decorative carrier drift, but its height SHALL remain driven by measured microphone levels. Measurement SHALL reuse the existing stream, avoid blocking the audio callback on UI work, and keep pending visual measurements bounded.

#### Scenario: Speech and silence
- **WHEN** the user speaks and then pauses
- **THEN** waveform amplitude responds to activity and smoothly decays toward a quiet line

#### Scenario: Slow UI
- **WHEN** rendering cannot keep pace with incoming audio
- **THEN** visual updates remain bounded, no second microphone stream opens, and recording and streaming continue

### Requirement: Actual operation lifecycle
After the stop request the panel SHALL leave listening and show “Transcribing…” with a horizontal line and glowing bead that moves left-to-right and loops while the operation continues. The bead SHALL indicate activity rather than a completion percentage, and SHALL replace the listening waveform during processing. Local whisper.cpp processing SHALL show “Transcribing locally…” in silver, using the operation's captured mode. Text delivery SHALL dismiss the panel without a fade before pasting, waiting up to 300 ms for native hide acknowledgment without blocking delivery on a UI failure. No speech or empty output SHALL fade away. Cancellation SHALL dismiss immediately without waiting for backend cleanup. Failure SHALL dismiss and preserve existing error feedback. Earlier dictation callbacks SHALL NOT update or hide a newer panel.

#### Scenario: Streaming fallback
- **WHEN** streaming falls back to cloud batch transcription
- **THEN** “Transcribing…” remains until the result is ready for delivery

#### Scenario: Local operation and settings change
- **WHEN** local transcription continues while settings select a different next-recording mode
- **THEN** the panel keeps “Transcribing locally…” and silver for the current operation

#### Scenario: Completion and failure
- **WHEN** an operation finishes, returns no speech or text, or fails
- **THEN** listening clears, text delivery hides before paste, no-speech/empty output fades away, and failure dismisses while existing error feedback remains

#### Scenario: Cancel recording or processing
- **WHEN** an active recording or transcription is cancelled
- **THEN** the panel disappears immediately and queued updates cannot restore it

#### Scenario: Rapid consecutive recordings
- **WHEN** a new recording starts with an earlier fade, completion, or UI update pending
- **THEN** earlier callbacks cannot hide or update the new panel

### Requirement: Focus and context isolation
The panel SHALL be non-activating and click-through outside its top-right cancel button. Showing, updating, and dismissing it SHALL preserve application focus, insertion cursor, hotkeys, clipboard restoration, and paste destination. Vox screenshot/OCR context SHALL exclude overlay pixels and status text. The panel SHALL NOT become the detected focused window.

#### Scenario: Focus and text delivery
- **WHEN** the user dictates into an editor or terminal and clicks through the panel outside the cancel button
- **THEN** the underlying app receives the click and existing text delivery, cursor, shortcuts, and clipboard restoration behavior is preserved

#### Scenario: Context capture
- **WHEN** context is captured with the overlay visible during recording or streaming fallback
- **THEN** images and extracted text contain target app context without overlay pixels or status words, and window detection still identifies the target app

### Requirement: Motion and resource cleanup
The panel SHALL respect OS reduced motion using a static presentation and immediate transitions. Animation SHALL stop while hidden. Disable and quit SHALL dismiss, stop timers, detach level consumption, and invalidate pending UI and fade callbacks. Enabling during an operation SHALL take effect at the next recording.

#### Scenario: Reduced motion changes
- **WHEN** reduced motion is enabled before or during dictation
- **THEN** decorative animation and fading stop while correct status remains visible

#### Scenario: Disable or quit
- **WHEN** disable is applied during dictation or Vox quits
- **THEN** the overlay is dismissed and visual resources are released without interrupting dictation on disable or permitting late callbacks to show it again

#### Scenario: Hidden panel
- **WHEN** dismissal finishes
- **THEN** no overlay animation work continues

### Requirement: Platform and failure isolation
Linux dictation SHALL operate without initializing macOS GUI components. Linux/X11 SHALL render through the existing GTK main loop. Headless and unsupported displays SHALL operate without initializing an overlay backend. Overlay initialization, measurement, drawing, dispatch, and teardown failures SHALL leave dictation usable. Documentation SHALL identify platform coverage and distinguish performed automated and manual verification from unavailable checks.

#### Scenario: Unsupported or failed backend
- **WHEN** an enabled preference encounters no supported desktop or an overlay backend failure
- **THEN** recording, transcription, text delivery, settings persistence, and existing error feedback remain usable

#### Scenario: Linux X11 overlay
- **WHEN** a Linux/X11 user enables the overlay and records
- **THEN** the native GTK panel shows the same statuses and actual microphone feedback, remains click-through outside the cancel button without changing focus, stays within the target monitor work area, and uses the shared lifecycle and screenshot guard

#### Scenario: Linux reduced motion
- **WHEN** the desktop disables GTK animations before or during dictation
- **THEN** the panel uses static presentation and immediate completion, with no hidden timer work

### Requirement: Explicit cancellation
The overlay SHALL provide a small top-right × button while recording and transcribing on macOS and Linux/X11, with an accessible Cancel label. Clicking it SHALL dismiss the overlay and cancel the associated operation without pasting or taking focus. The rest of the panel SHALL remain click-through. The button SHALL follow capture hiding, placement and teardown of the panel. Double-tap cancellation SHALL default off for both hotkeys; strict boolean `[hotkey] double_tap_cancel = true` SHALL opt in using the existing timeout and apply on config reload.

#### Scenario: Cancel using the overlay
- **WHEN** the user clicks × while recording or transcribing
- **THEN** that operation is cancelled, the overlay disappears, no text is pasted, and the target app retains focus

#### Scenario: Stale click
- **WHEN** a click belongs to an earlier operation or a dismissed overlay
- **THEN** it cannot cancel a newer operation or history retry

#### Scenario: Default double tap
- **WHEN** no double-tap preference is configured and either hotkey is tapped twice quickly
- **THEN** both taps use normal toggle behavior and no cancel event is emitted

#### Scenario: Explicit double-tap opt in
- **WHEN** double_tap_cancel is set to true and reloaded
- **THEN** the primary and fallback hotkeys use the configured double-tap timeout to cancel
