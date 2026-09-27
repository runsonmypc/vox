## MODIFIED Requirements

### Requirement: Audio Feedback Sounds
The system SHALL play a short sound for start, stop, error, busy, cancel, pause and resume while `[sounds] enabled` is on, SHALL follow changes to that setting without a restart, and SHALL let the user replace any of these sounds. On Linux the built-in sounds SHALL resemble the macOS alert sounds, and SHALL be synthesized by Vox from its own parameters, with no audio taken or derived from Apple's sound files.

#### Scenario: Built-in sounds
- **WHEN** no custom sound is installed
- **THEN** macOS plays its system alert sounds (start Tink, stop Pop, error Basso, busy Funk, cancel Blow, pause Bottle, resume Glass), and Linux plays sounds that Vox synthesizes when it starts, each resembling its macOS counterpart in pitch, timbre, length, envelope and loudness

#### Scenario: Built-in sounds on Linux
- **WHEN** Vox makes its built-in sounds on Linux
- **THEN** each one starts and ends on silence, peaks at least 6 dB below full scale, lasts less than 1.6 seconds (the start sound less than 0.1 seconds), and is the same at every start

#### Scenario: Custom sound
- **WHEN** `~/.config/vox/sounds/<name>.wav` exists for one of the sound names (`start`, `stop`, `error`, `busy`, `cancel`, `pause`, `resume`)
- **THEN** Vox plays that file instead of the built-in sound, and the device rescan on returning to idle does not cut it short (see "Input Device Rescan")

#### Scenario: Sounds turned off
- **WHEN** `[sounds] enabled = false`, including after an edit while Vox runs
- **THEN** no sound plays
