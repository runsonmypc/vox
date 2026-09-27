## ADDED Requirements

### Requirement: Hotkey Selection from Tray
The system SHALL provide "Set Hotkey…" in the tray menu, right after "Set API Key…" in the windows section, available only while Vox is idle. It SHALL open a window that records the key tapped on its own (`[hotkey] key`) and an optional key combination (`[hotkey] fallback`) as the hotkey listener names them, SHALL refuse keys that fire while the user types, and SHALL save to `config.toml`, keeping the rest of the file and its comments. The change SHALL apply when the window closes, with no restart, and the hotkey SHALL NOT start or stop dictation while the window is open.

#### Scenario: Setting a key
- **WHEN** the user clicks Hotkey, taps Right Command on its own, and clicks Save
- **THEN** `[hotkey] key` is saved as `cmd_r`, the window closes, tapping Right Command starts dictation at once, and the old key no longer does

#### Scenario: Setting a key combination
- **WHEN** the user clicks Key combination, holds Control, presses Space, and clicks Save
- **THEN** `[hotkey] fallback` is saved as `ctrl+space`, pressing it starts and stops dictation, pressing it twice quickly cancels, and the tap-alone key still works

#### Scenario: Removing the combination
- **WHEN** the user clicks the clear button next to the key combination and clicks Save
- **THEN** `fallback` is removed from `[hotkey]`

#### Scenario: Back to the default
- **WHEN** the user clicks Use Default and clicks Save
- **THEN** the hotkey is `right_shift` with no combination, and Use Default is disabled while that is already the setting

#### Scenario: A typing key is refused
- **WHEN** the Hotkey field records and the user presses a key that types or edits text, such as a letter, a digit, Space, Return, Tab, an arrow, Caps Lock or fn
- **THEN** the window says that Vox Transfer needs a key you don't type with (Shift, Control, Option or Command on either side, or F1 to F20 on macOS; Shift, Ctrl, Alt or Super on either side, AltGr, or F1 to F20 on Linux), keeps the old key, and keeps recording, so the next key can be pressed at once
- **AND** pressing two keys together in the Hotkey field says that the hotkey is a single key and points to Key combination

#### Scenario: An unusable combination is refused
- **WHEN** the Key combination field records Shift with Space, a modifier with a letter, modifiers alone, a function key alone, or AltGr with Space
- **THEN** the window says that a combination holds Control, Option or Command (Ctrl, Alt or Super on Linux) and ends with Space or F1 to F20, and keeps recording

#### Scenario: A combination that includes the hotkey
- **WHEN** the key combination includes the tap-alone key, such as Left Control with `ctrl+space`
- **THEN** the window shows "The key combination can’t include the hotkey, Left Control." in red, and Save leaves the window open and writes nothing

#### Scenario: Escape while recording
- **WHEN** a field records and the user presses Esc, clicks the field again, clicks another control, or the window loses focus
- **THEN** recording stops, the old value stays, and the window stays open; with no field recording, Esc closes the window without saving

#### Scenario: The hotkey while the window is open
- **WHEN** the user presses the current hotkey or combination while the window is open
- **THEN** no dictation starts or stops, and no sound plays

#### Scenario: The window closes by any means, or fails to open
- **WHEN** the window is saved, cancelled, closed, crashes or is force-quit, or its process cannot be started
- **THEN** the hotkey works again with the settings in `config.toml`

#### Scenario: A recording running as the window opens
- **WHEN** a recording starts in the moment between choosing Set Hotkey… and the window opening
- **THEN** that recording is discarded with the cancel sound, and nothing is transcribed

#### Scenario: While recording or processing
- **WHEN** Vox is recording or processing a dictation
- **THEN** Set Hotkey… is disabled until it returns to idle

#### Scenario: Settings file fails to load
- **WHEN** `config.toml` cannot be parsed or fails validation
- **THEN** the window says it couldn't read the file (an alert, and a red status line on macOS or a banner on Linux), disables both fields, Use Default and Save, and writes nothing

#### Scenario: Nothing changed
- **WHEN** the user clicks Save without changing the hotkey or the combination
- **THEN** the window closes and writes nothing, so no `config.toml` is created for a user who has none

#### Scenario: Warnings
- **WHEN** the tap-alone key is F1 to F12, or on Linux the left Super key or an Alt key
- **THEN** the window shows an orange warning (on macOS, that most keyboards send F1 to F12 only while fn is held unless the standard-function-keys setting is on; on Linux, that apps receive F1 to F12 too, that GNOME and KDE open their overview on a Super tap, or that some apps show their menu bar on an Alt tap), and saving is still allowed
