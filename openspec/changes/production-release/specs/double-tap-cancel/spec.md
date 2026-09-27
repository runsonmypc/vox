## MODIFIED Requirements

### Requirement: Cancellation of Active Processing and Transcription
When a cancellation event is triggered during the `PROCESSING` state, the system SHALL immediately cancel ongoing batch, streaming or local transcription tasks, discard any received transcription output, suppress clipboard modifications and paste keystrokes that have not started yet, and transition to `IDLE`.

#### Scenario: Double-tap cancels in-flight batch transcription
- **WHEN** a double-tap cancellation is detected while Whisper batch transcription is active
- **THEN** the processing task is cancelled, no text is placed on the clipboard or pasted, and the state reverts to `IDLE`

#### Scenario: Double-tap cancels streaming finalization
- **WHEN** a double-tap cancellation is detected while awaiting final streaming transcript completion
- **THEN** the completion awaiter is aborted, the WebSocket connection is closed, and text injection is bypassed

#### Scenario: Double-tap cancels local transcription
- **WHEN** a double-tap cancellation is detected while a local whisper.cpp transcription runs
- **THEN** the whisper.cpp process is killed and waited for, and nothing is pasted

#### Scenario: Cancel after the paste has begun
- **WHEN** a cancellation arrives after the paste keystroke has already been sent
- **THEN** the text stays pasted, the user's previous clipboard is still restored before any later paste starts, and the state reverts to `IDLE`

### Requirement: Cancellation Audio Feedback
The system SHALL provide immediate audible confirmation when a recording or processing operation is cancelled, playing a distinct cancellation sound when sounds are enabled. The sound SHALL follow the `[sounds] enabled` setting as it is at that moment, and a user-provided sound file SHALL replace the built-in one.

#### Scenario: Cancellation sound played upon successful cancel
- **WHEN** a double-tap cancellation successfully aborts recording or processing while sounds are enabled
- **THEN** the system plays a distinct cancellation sound (such as the native macOS alert sound or a synthetic descending tone)

#### Scenario: Cancellation sound suppressed when sounds are disabled
- **WHEN** a cancellation occurs while `[sounds] enabled` is set to false
- **THEN** the cancellation completes silently without playing any audio feedback

#### Scenario: Sounds switched in a running Vox
- **WHEN** the user changes `[sounds] enabled` in `config.toml` while Vox runs
- **THEN** the next cancellation follows the new setting without a restart

#### Scenario: Custom cancellation sound
- **WHEN** `~/.config/vox/sounds/cancel.wav` exists
- **THEN** that file plays instead of the built-in cancellation sound
