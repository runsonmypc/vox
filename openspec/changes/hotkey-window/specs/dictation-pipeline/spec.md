## MODIFIED Requirements

### Requirement: Configuration File Errors
The system SHALL check the type and range of every setting in `config.toml`. When the file cannot be parsed or validated at startup, it SHALL log the error naming the file and the setting, start on the default settings, and refuse to record until the file loads, because the user's real settings, such as local-only transcription, are unknown. It SHALL NOT exit, so the login service does not restart it in a loop.

#### Scenario: Invalid setting at startup
- **WHEN** Vox starts and `config.toml` cannot be parsed, or a setting has the wrong type or range (for example `level = "0.5"`, `level = 50` or `max_recording_seconds = 0`)
- **THEN** Vox logs an error such as `[attenuation] level must be a number from 0 to 1, not "0.5"`, keeps running on defaults, and the tray's status line shows "Settings file has an error"

#### Scenario: Hotkey while the file is broken
- **WHEN** the user presses the hotkey while the settings file could not be loaded
- **THEN** the error sound plays, the reason is logged, and no recording starts, so no audio is sent anywhere

#### Scenario: File fixed while running
- **WHEN** the user fixes `config.toml` while Vox runs
- **THEN** within a few seconds Vox applies it, including `[hotkey]`, clears the error, and updates the status line, with no restart

#### Scenario: Permanent startup failure
- **WHEN** a required system tool is missing (for example `xdotool` or `xclip` on Linux) or the lock directory cannot be created
- **THEN** Vox logs what is missing and exits with status 78, which the Linux login service does not retry

#### Scenario: xprop missing on Linux
- **WHEN** `xprop` (package x11-utils) is not installed on Linux
- **THEN** Vox starts and logs one warning that it cannot tell terminals from other windows, so terminals get `Ctrl+V` and `[window_classes]` does not apply; this is not a status-78 startup failure

### Requirement: Settings Reload
The system SHALL apply every edit to `config.toml` while running, a few seconds after a save, with no restart.

#### Scenario: Hot-reloaded settings
- **WHEN** the user changes the dictionary, snippets, window classes, screen hints, sounds, attenuation, the recording limit, the transcription mode, a model, the language or the prompt
- **THEN** the next dictation uses the new values without a restart

#### Scenario: Audio settings during a recording
- **WHEN** `[audio]` device, sample rate or channels change while a recording runs
- **THEN** the change is applied once that recording ends, and the recording is not lost

#### Scenario: Hotkey settings
- **WHEN** `[hotkey]` key, fallback or double_tap_timeout_ms changes, by hand or from the hotkey window
- **THEN** within a few seconds, or at once when the hotkey window closes, a new hotkey listener with those settings replaces the old one, with no restart, and the old key no longer toggles dictation
- **AND** during a recording the new listener takes over at once, and the new key stops the recording

#### Scenario: Invalid edit while running
- **WHEN** a `config.toml` that loaded is edited into an invalid state while Vox runs
- **THEN** Vox logs a warning naming the file and the setting and keeps the settings it has

### Requirement: Hotkey Names
The system SHALL accept the hotkey names users naturally write and SHALL match them to the key that was actually pressed. Without a usable keyboard backend it SHALL exit with a clear message.

#### Scenario: Spellings
- **WHEN** `[hotkey] key` is `right_shift`, `"Right Shift"`, `right_ctrl`, `right_alt`, `left_shift`, `left_ctrl` or `left_alt`, or `fallback` is a combination such as `"left_ctrl+space"`
- **THEN** each names the intended key

#### Scenario: Left and right modifiers
- **WHEN** the hotkey is `left_shift`
- **THEN** it fires on the left Shift key and not on the right one

#### Scenario: Alt with Shift held on Linux
- **WHEN** an Alt key is pressed or released while Shift is held, which X11 reports as Meta
- **THEN** it is still named `alt` or `right_alt`, so `alt+shift+space` fires whichever of Alt and Shift goes down first, and an Alt let go after Shift never stays counted as held

#### Scenario: No keyboard backend
- **WHEN** global hotkeys cannot be set up, for example with no X11 display on Linux
- **THEN** Vox logs that global hotkeys are unavailable, with the reason in a few words (such as "no X display: DISPLAY is not set"), and that it needs an X11 display, and exits with status 1 instead of a traceback
