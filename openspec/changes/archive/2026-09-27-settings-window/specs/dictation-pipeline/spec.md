## MODIFIED Requirements

### Requirement: Recording Limit in Settings
The General page of Settings SHALL offer a Recording Limit choice of 5, 10, 15, 30 and 60 minutes, showing the current limit, and SHALL save a choice to `[audio] max_recording_seconds`.

#### Scenario: Picking a limit
- **WHEN** the user picks a limit in Settings
- **THEN** it is saved to `[audio] max_recording_seconds` with the rest of `config.toml` and its comments kept, the choice shows it, and it applies from the next recording

#### Scenario: Custom limit from the file
- **WHEN** `config.toml` sets a limit that is not one of the choices
- **THEN** the choice also lists that value, selected

#### Scenario: Limit cannot be saved
- **WHEN** the chosen limit cannot be written to `config.toml`
- **THEN** the window shows a "Couldn't Save" message, and the limit stays as it was

### Requirement: Settings Reload
The system SHALL apply every edit to `config.toml` while running, a few seconds after a save, with no restart, and SHALL apply the file at once when the Settings window closes.

#### Scenario: Hot-reloaded settings
- **WHEN** the user changes the dictionary, snippets, window classes, screen hints, sounds, attenuation, the recording limit, the transcription mode, a model, the language or the prompt, by hand or in Settings
- **THEN** the next dictation uses the new values without a restart

#### Scenario: Settings window closes
- **WHEN** the Settings window closes, however it closes
- **THEN** Vox reads `config.toml` and the API key again at once and applies them as a reload does, so a dictation started right after closing uses everything saved in the window

#### Scenario: Audio settings during a recording
- **WHEN** `[audio]` device, sample rate or channels change while a recording runs
- **THEN** the change is applied once that recording ends, and the recording is not lost

#### Scenario: Hotkey settings
- **WHEN** `[hotkey]` key, fallback or double_tap_timeout_ms changes, by hand or on the Hotkey page of Settings
- **THEN** within a few seconds, or at once when the Settings window closes, a new hotkey listener with those settings replaces the old one, with no restart, and the old key no longer toggles dictation
- **AND** during a recording the new listener takes over at once, and the new key stops the recording

#### Scenario: Invalid edit while running
- **WHEN** a `config.toml` that loaded is edited into an invalid state while Vox runs
- **THEN** Vox logs a warning naming the file and the setting and keeps the settings it has

### Requirement: Input Device Rescan
The system SHALL rescan the audio input devices so a microphone picked by name that is connected after Vox started can record, without disturbing a recording or delaying the hotkey.

#### Scenario: Startup and return to idle
- **WHEN** Vox starts, or returns to idle after a dictation (at least 1 second later, and never before 0.1 seconds after a sound still playing through the audio system has ended, such as a long custom sound on Linux)
- **THEN** it re-initialises the audio system and lists the input devices, so the next recording finds a device connected since

#### Scenario: Periodic rescan on macOS
- **WHEN** Vox stays idle on macOS
- **THEN** it also rescans every 30 seconds, while on Linux, where the default PipeWire or PulseAudio device already follows hotplugged devices, it rescans only on returning to idle

#### Scenario: Never during a recording
- **WHEN** an audio stream is open
- **THEN** no rescan re-initialises the audio system

#### Scenario: Hotkey during a rescan
- **WHEN** the hotkey is pressed while a rescan is pending or running
- **THEN** a pending rescan is dropped, and the hotkey waits at most for a running rescan to finish

## RENAMED Requirements

- FROM: `### Requirement: Recording Limit from the Tray`
- TO: `### Requirement: Recording Limit in Settings`
