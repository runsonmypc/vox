## MODIFIED Requirements

### Requirement: General Settings
The General page of Settings SHALL let the user choose the microphone, the recording limit, whether sounds play, whether and how far Vox lowers the system volume while recording, whether screen hints are used, and whether the recording overlay is shown.

#### Scenario: Sounds
- **WHEN** the user turns Sounds off
- **THEN** `[sounds] enabled = false` is saved, and no sound plays after the window closes

#### Scenario: Lowering the volume
- **WHEN** the user turns "Lower other audio while recording" on and sets the slider to 30%
- **THEN** `[attenuation] enabled = true` and `level = 0.3` are saved, the slider goes from 0% to 100% in steps of 5%, and it is disabled while lowering is off

#### Scenario: Slider saves once
- **WHEN** the user drags the volume slider
- **THEN** the value is saved when the slider is released, not for every step it passes

#### Scenario: Screen hints
- **WHEN** the user turns Screen hints off
- **THEN** `[context] screen = false` is saved, and the page links to what screen hints send (the Privacy documentation)

#### Scenario: A level from the file that is not a step
- **WHEN** `config.toml` sets `[attenuation] level = 0.33`
- **THEN** the slider shows 33%, and the value in the file is kept until the user moves the slider

#### Scenario: Overlay opt in
- **WHEN** the user enables “Show recording overlay” on macOS or Linux/X11
- **THEN** `[overlay] enabled = true` is persisted through the shared settings path and applied without restarting

#### Scenario: Default on
- **WHEN** the configuration has no overlay preference
- **THEN** the setting is on and the overlay appears on supported desktops during recording

#### Scenario: Save failure
- **WHEN** saving the preference fails or the settings file is invalid
- **THEN** Settings reports its existing error and keeps the control consistent with persisted configuration

#### Scenario: Linux availability
- **WHEN** the user opens General Settings on Linux/X11
- **THEN** the overlay control is available and persists the same shared preference

#### Scenario: Unsupported Linux session
- **WHEN** General Settings runs on a non-X11 Linux display
- **THEN** the overlay control is unavailable with a short X11 availability note

### Requirement: Hotkey Selection in Settings
The Hotkey page of Settings SHALL record the key tapped on its own (`[hotkey] key`) and an optional key combination (`[hotkey] fallback`) as the hotkey listener names them, SHALL refuse keys that fire while the user types, and SHALL save each accepted recording to `config.toml` at once, keeping the rest of the file and its comments. The new hotkey SHALL apply when the Settings window closes, with no restart.

#### Scenario: Setting a key
- **WHEN** the user clicks Hotkey and taps Right Command on its own
- **THEN** `[hotkey] key` is saved as `cmd_r`, and once the window closes, tapping Right Command starts dictation at once and the old key no longer does

#### Scenario: Setting a key combination
- **WHEN** the user clicks Key combination, holds Control and presses Space
- **THEN** `[hotkey] fallback` is saved as `ctrl+space`, and once the window closes, pressing it starts and stops dictation, pressing it twice quickly cancels only when `[hotkey] double_tap_cancel = true`, and the tap-alone key still works

#### Scenario: Removing the combination
- **WHEN** the user clicks the clear button next to the key combination
- **THEN** `fallback` is removed from `[hotkey]`

#### Scenario: Back to the default
- **WHEN** the user clicks Use Default
- **THEN** the hotkey is saved as `right_shift` with no combination, and Use Default is disabled while that is already the setting

#### Scenario: A typing key is refused
- **WHEN** the Hotkey field records and the user presses a key that types or edits text, such as a letter, a digit, Space, Return, Tab, an arrow or Caps Lock
- **THEN** the page says that Vox Transfer needs a key you don't type with (Shift, Control, Option or Command on either side, fn (Globe), or F1 to F20 on macOS; Shift, Ctrl, Alt or Super on either side, AltGr, Pause, Scroll Lock, or F1 to F20 on Linux), keeps and saves nothing new, and keeps recording, so the next key can be pressed at once
- **AND** pressing two keys together in the Hotkey field says that the hotkey is a single key and points to Key combination

#### Scenario: Pause and Scroll Lock on Linux
- **WHEN** the Hotkey field records on Linux and the user taps Pause or Scroll Lock
- **THEN** the key is saved as `pause` or `scroll_lock` and shown as Pause or Scroll Lock, with no warning

#### Scenario: fn on macOS
- **WHEN** the Hotkey field records on macOS and the user taps fn (Globe) on its own
- **THEN** the key is saved as `fn` and shown as "fn (Globe)"
- **AND** holding fn and pressing another key, such as a function key or Right Command, records only that other key, because Mac laptops need fn for the function keys

#### Scenario: An unusable combination is refused
- **WHEN** the Key combination field records Shift with Space, a modifier with a letter, modifiers alone, a function key alone, AltGr with Space, fn tapped on its own, or a modifier with Pause or Scroll Lock
- **THEN** the page says that a combination holds Control, Option or Command (Ctrl, Alt or Super on Linux) and ends with Space or F1 to F20, and keeps recording
- **AND** fn held while a combination is pressed is left out of it, so holding Control and fn and pressing F5 records `ctrl+f5`

#### Scenario: A combination that includes the hotkey
- **WHEN** a recording would make the key combination include the tap-alone key, such as Left Control as the hotkey with `ctrl+space` as the combination
- **THEN** the page shows "The key combination can’t include the hotkey, Left Control." in red, recording stops, nothing is saved, and both fields keep their saved values

#### Scenario: Escape while recording
- **WHEN** a field records and the user presses Esc, clicks the field again, clicks another control, switches page, or the window loses focus
- **THEN** recording stops, the saved value stays, and the window stays open

#### Scenario: While a field records
- **WHEN** a field is recording
- **THEN** the window takes every key, so Return, Space, Tab and the window's own shortcuts (such as Cmd+W or Ctrl+W) are recorded or refused as keys and do nothing else

#### Scenario: Settings file fails to load
- **WHEN** `config.toml` cannot be parsed or fails validation
- **THEN** both fields and Use Default are disabled, and nothing is written

#### Scenario: Warnings
- **WHEN** the tap-alone key is F1 to F12, fn on macOS, or on Linux the left Super key or an Alt key
- **THEN** the page shows an orange warning (on macOS, that most keyboards send F1 to F12 only while fn is held unless the standard-function-keys setting is on, or for fn, that macOS acts on fn too, so System Settings > Keyboard > "Press 🌐 key to" (or "Press fn key to") should be "Do Nothing" and a Dictation shortcut that presses 🌐 or fn twice should be changed; on Linux, that apps receive F1 to F12 too, that GNOME and KDE open their overview on a Super tap, or that some apps show their menu bar on an Alt tap), and the key is still saved
