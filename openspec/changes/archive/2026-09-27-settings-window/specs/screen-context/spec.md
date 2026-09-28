## MODIFIED Requirements

### Requirement: Screen Hints Setting
The system SHALL provide a `[context] screen` setting, on by default, that controls whether window-title words and focused-window text are used as transcription hints, and SHALL let the user turn it on and off on the General page of Settings. A change to the setting SHALL apply from the next dictation without a restart.

#### Scenario: Default
- **WHEN** `config.toml` does not set `[context] screen`
- **THEN** screen hints are on

#### Scenario: Turned off
- **WHEN** `[context] screen = false`
- **THEN** no window-title words and no screen, AT-SPI, tmux or OCR text go to any provider, and no screen capture runs at all, so macOS never asks for the Screen Recording permission

#### Scenario: Paste shortcut still chosen
- **WHEN** screen hints are off
- **THEN** Vox still detects, on the computer, which kind of app has focus, so it can pick the right paste shortcut

#### Scenario: Changed while running
- **WHEN** the user edits `[context] screen` in `config.toml`, or turns Screen hints on or off in Settings, while Vox runs
- **THEN** the next dictation follows the new value
