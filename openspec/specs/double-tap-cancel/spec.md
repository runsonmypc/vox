## Purpose

Provides quick cancellation of in-flight voice recordings and active transcription processing via double-tapping the configured hotkey, cleanly discarding captured audio, restoring audio attenuation, and suppressing text injection.

## Requirements

### Requirement: Double-Tap Hotkey Detection
The system SHALL detect two consecutive taps of the configured primary hotkey or fallback key combination occurring within a defined timeout threshold (default 400 milliseconds) as a cancellation request.

#### Scenario: Hotkey pressed twice within timeout window
- **WHEN** the hotkey is tapped and released, and tapped again within 400 milliseconds while recording or processing
- **THEN** the system recognizes the input sequence as a double-tap cancellation event rather than a standard toggle action

#### Scenario: Hotkey pressed outside timeout window treated as distinct actions
- **WHEN** the second hotkey press occurs after the double-tap timeout threshold has elapsed
- **THEN** the system processes each hotkey actuation independently according to the current state machine rules

#### Scenario: Intervening key press cancels double-tap sequence
- **WHEN** any other key is pressed between the first and second hotkey actuations
- **THEN** the double-tap sequence is invalidated and subsequent presses are evaluated anew

### Requirement: Cancellation of Active Recording
When a cancellation event is triggered during the `RECORDING` state, the system SHALL immediately terminate audio capture, discard any buffered audio frames, cancel active streaming workers, restore any attenuated system volume, and transition to `IDLE` without transcribing.

#### Scenario: Double-tap cancels active audio recording
- **WHEN** a double-tap cancellation is detected while the system is in the `RECORDING` state
- **THEN** audio capture is stopped, recorded audio buffers are discarded, no transcription task is initiated, and the state reverts to `IDLE`

#### Scenario: Volume attenuation restored on recording cancellation
- **WHEN** a double-tap cancellation occurs while system volume is attenuated for recording
- **THEN** the system volume is immediately restored to its pre-recording level

#### Scenario: Real-time streaming worker aborted on recording cancellation
- **WHEN** a double-tap cancellation occurs while streaming audio chunks to a real-time transcription service
- **THEN** the streaming background task is cancelled, the WebSocket session is closed, and any accumulated partial transcript is discarded

### Requirement: Cancellation of Active Processing and Transcription
When a cancellation event is triggered during the `PROCESSING` state, the system SHALL immediately cancel ongoing batch or streaming transcription tasks, discard any received transcription output, suppress clipboard modifications and paste keystrokes, and transition to `IDLE`.

#### Scenario: Double-tap cancels in-flight batch transcription
- **WHEN** a double-tap cancellation is detected while Whisper batch transcription is active
- **THEN** the processing task is cancelled, no text is placed on the clipboard or pasted, and the state reverts to `IDLE`

#### Scenario: Double-tap cancels streaming finalization
- **WHEN** a double-tap cancellation is detected while awaiting final streaming transcript completion
- **THEN** the completion awaiter is aborted, the WebSocket connection is closed, and text injection is bypassed

### Requirement: Cancellation Audio Feedback
The system SHALL provide immediate audible confirmation when a recording or processing operation is cancelled, playing a distinct cancellation sound when sounds are enabled.

#### Scenario: Cancellation sound played upon successful cancel
- **WHEN** a double-tap cancellation successfully aborts recording or processing while sounds are enabled
- **THEN** the system plays a distinct cancellation sound (such as the native macOS alert sound or a synthetic descending tone)

#### Scenario: Cancellation sound suppressed when sounds are disabled
- **WHEN** a cancellation occurs while `sounds_enabled` is set to false
- **THEN** the cancellation completes silently without playing any audio feedback
