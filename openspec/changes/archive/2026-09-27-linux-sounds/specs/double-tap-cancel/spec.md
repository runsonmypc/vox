## MODIFIED Requirements

### Requirement: Cancellation Audio Feedback
The system SHALL provide immediate audible confirmation when a recording or processing operation is cancelled, playing a distinct cancellation sound when sounds are enabled. The sound SHALL follow the `[sounds] enabled` setting as it is at that moment, and a user-provided sound file SHALL replace the built-in one.

#### Scenario: Cancellation sound played upon successful cancel
- **WHEN** a double-tap cancellation successfully aborts recording or processing while sounds are enabled
- **THEN** the system plays a distinct cancellation sound: the macOS alert sound Blow, or on Linux Vox's own sound that resembles it

#### Scenario: Cancellation sound suppressed when sounds are disabled
- **WHEN** a cancellation occurs while `[sounds] enabled` is set to false
- **THEN** the cancellation completes silently without playing any audio feedback

#### Scenario: Sounds switched in a running Vox
- **WHEN** the user changes `[sounds] enabled` in `config.toml` while Vox runs
- **THEN** the next cancellation follows the new setting without a restart

#### Scenario: Custom cancellation sound
- **WHEN** `~/.config/vox/sounds/cancel.wav` exists
- **THEN** that file plays instead of the built-in cancellation sound
