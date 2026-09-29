## ADDED Requirements

### Requirement: Settings Window
The system SHALL provide one Settings window, native to each platform (AppKit on macOS, GTK 4 with libadwaita on Linux), with the pages General, Hotkey, Transcription, Vocabulary and Snippets. It SHALL open from a "Settings…" tray item that is available only while Vox is idle. The window SHALL apply each change as it is made, with no Save button, and SHALL write it to `config.toml`, keeping the rest of the file and its comments. It SHALL never change a setting it does not show, and SHALL NOT create `config.toml` when nothing was changed. The hotkey SHALL NOT start or stop dictation while the window is open.

#### Scenario: Opening Settings
- **WHEN** Vox is idle and the user chooses Settings… from the tray menu
- **THEN** the Settings window opens on the General page, or comes to the front if it is already open

#### Scenario: While recording or processing
- **WHEN** Vox is recording or processing a dictation
- **THEN** Settings… is disabled until Vox returns to idle

#### Scenario: Tray menu
- **WHEN** the user opens the tray menu
- **THEN** it shows, in order: the status line; "Set API Key…" only while the key is missing or unreadable; Pause Dictation; the recent dictations; Search History… and Settings…; and Quit Vox Transfer
- **AND** it has no Input Device, Transcription or Recording Limit submenu, and no Vocabulary & Snippets…, Set Hotkey… or separate Set API Key… item

#### Scenario: Missing key shortcut
- **WHEN** the user chooses "Set API Key…" at the top of the menu
- **THEN** Settings opens on the Transcription page

#### Scenario: A change applies as it is made
- **WHEN** the user changes a setting, such as turning Sounds off
- **THEN** `config.toml` holds the new value straight away, with every other line and comment unchanged, and the next dictation after the window closes uses it

#### Scenario: Text fields
- **WHEN** the user edits a text field, such as the prompt
- **THEN** the value is saved when the user presses Return, leaves the field, or closes the window, and not on every keystroke

#### Scenario: Settings the window does not show
- **WHEN** `config.toml` sets `sample_rate`, `channels`, `double_tap_timeout_ms`, `streaming_model`, `[whisper] model` or `[window_classes]`, and the user changes other settings in the window
- **THEN** those settings keep their values and spelling in the file

#### Scenario: Nothing changed
- **WHEN** a user without a `config.toml` opens Settings and closes it without changing anything
- **THEN** no `config.toml` is created

#### Scenario: Dictation while Settings is open
- **WHEN** the user presses the hotkey or the key combination while Settings is open
- **THEN** no dictation starts or stops, and no sound plays

#### Scenario: A recording running as Settings opens
- **WHEN** a recording starts in the moment between choosing Settings… and the window opening
- **THEN** that recording is discarded with the cancel sound, and nothing is transcribed

#### Scenario: The window closes by any means, or fails to open
- **WHEN** the window is closed, crashes or is force-quit, or its process cannot be started
- **THEN** the hotkey works again, and Vox uses the API key and the settings in `config.toml` at once, without waiting for the next settings reload

#### Scenario: A change that cannot be saved
- **WHEN** writing `config.toml` fails, for example because its folder is read-only
- **THEN** the window shows a "Couldn't Save" message naming the problem, and the control goes back to the value in the file

#### Scenario: Settings file fails to load
- **WHEN** `config.toml` cannot be parsed or fails validation while Settings opens or while it is open
- **THEN** the window says it couldn't read the file and names the problem (a red status line on macOS, a banner on Linux), disables every control that writes `config.toml`, and writes nothing; the API key, which is not stored in `config.toml`, can still be set or removed

#### Scenario: File edited while the window is open
- **WHEN** `config.toml` is changed by hand while Settings is open
- **THEN** the window shows the new values within a few seconds, and a later change in the window keeps the hand edit

### Requirement: General Settings
The General page of Settings SHALL let the user choose the microphone, the recording limit, whether sounds play, whether and how far Vox lowers the system volume while recording, and whether screen hints are used.

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

### Requirement: Microphone Selection in Settings
The General page of Settings SHALL list the audio input devices, with System Default first, and SHALL save the chosen device to `[audio] device` by name. The window SHALL list the devices itself when it opens, and SHALL NOT depend on the tray or the daemon for the list.

#### Scenario: Switching input microphone
- **WHEN** the user picks a microphone in Settings
- **THEN** its name is saved to `[audio] device`, and the next recording after the window closes uses it without a restart

#### Scenario: System default
- **WHEN** the user picks System Default
- **THEN** `device` is removed from `[audio]`, and Vox records from the system's default input

#### Scenario: Device connected while Settings is open
- **WHEN** a microphone is connected or removed while Settings is open
- **THEN** the list shows the change after the user clicks Refresh next to it, or reopens Settings

#### Scenario: Saved device not connected
- **WHEN** `[audio] device` names a device that is not connected
- **THEN** the list shows that name with "(not connected)", selected, and Vox records from the system default until it is connected again

#### Scenario: Device given by index
- **WHEN** `[audio] device` is a number
- **THEN** the device with that index is selected, and choosing another device saves a name in its place

#### Scenario: Selection survives renumbering
- **WHEN** the user picks a device and the audio system later renumbers its devices
- **THEN** Vox still records from the device with that name, and Settings selects that device: an exact name match wins over a name that only contains it, and of two devices with the same name the first is selected, since it is the one that records

#### Scenario: Selection is kept
- **WHEN** the user picks a device in Settings and Vox restarts
- **THEN** Vox still records from that device

### Requirement: Transcription Selection in Settings
The Transcription page of Settings SHALL let the user choose OpenAI batch, OpenAI streaming, or local whisper.cpp transcription, the spoken language, the prompt, and the whisper.cpp program and model files, and SHALL save each choice to `config.toml`. Whether a mode can be chosen SHALL follow the same rules everywhere Vox checks it: the OpenAI modes need an API key, and the local mode needs a working whisper.cpp binary and model.

#### Scenario: Switching providers
- **WHEN** the user selects an available transcription mode
- **THEN** `[transcription] mode` is saved, and the next recording after the window closes uses that mode

#### Scenario: Unavailable mode
- **WHEN** the OpenAI API key or local whisper.cpp setup required by a mode is missing
- **THEN** that mode is disabled, the reason is shown under it, and the saved mode stays selected

#### Scenario: Setting up a mode on the same page
- **WHEN** the user saves an API key, or chooses a working whisper.cpp program and model, on the Transcription page
- **THEN** the mode that needed it becomes available at once, without reopening the window

#### Scenario: Configured mode cannot run
- **WHEN** the saved mode cannot run, for example because the whisper.cpp model file is missing
- **THEN** it stays selected, the page shows why it cannot run, and choosing another available mode clears the problem once the window closes

#### Scenario: whisper.cpp files
- **WHEN** the user clicks Choose… next to the whisper.cpp program or model
- **THEN** a file dialog opens, and the chosen path is saved to `[whisper_cpp] binary` or `model` as an absolute path, with a home-folder path written with `~`

#### Scenario: Language
- **WHEN** the user picks a language from the list
- **THEN** its ISO code is saved to `[transcription] language`, and "Detect automatically", first in the list, removes `language`
- **AND** a code in the file that is not in the list is shown as that code, selected, and kept

#### Scenario: Prompt
- **WHEN** the user edits the prompt and leaves the field
- **THEN** `[transcription] prompt` is saved, and an empty prompt removes it

### Requirement: Hotkey Selection in Settings
The Hotkey page of Settings SHALL record the key tapped on its own (`[hotkey] key`) and an optional key combination (`[hotkey] fallback`) as the hotkey listener names them, SHALL refuse keys that fire while the user types, and SHALL save each accepted recording to `config.toml` at once, keeping the rest of the file and its comments. The new hotkey SHALL apply when the Settings window closes, with no restart.

#### Scenario: Setting a key
- **WHEN** the user clicks Hotkey and taps Right Command on its own
- **THEN** `[hotkey] key` is saved as `cmd_r`, and once the window closes, tapping Right Command starts dictation at once and the old key no longer does

#### Scenario: Setting a key combination
- **WHEN** the user clicks Key combination, holds Control and presses Space
- **THEN** `[hotkey] fallback` is saved as `ctrl+space`, and once the window closes, pressing it starts and stops dictation, pressing it twice quickly cancels, and the tap-alone key still works

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

## MODIFIED Requirements

### Requirement: System Tray Status Indicator
The system SHALL provide a system tray icon on macOS (menu bar) and Linux (AppIndicator / StatusNotifierItem) that visually reflects the current daemon state. Wherever no graphical session or tray support is available, the daemon SHALL run headless as before. While the daemon is idle or paused, the first menu line SHALL name the most urgent problem that stops dictation, in this order: a settings file that could not be loaded (until it loads, the mode and so the need for a key are only the defaults), a missing or unreadable API key, a transcription mode that cannot run, that dictation is off while Settings is open, then a notice from the daemon.

#### Scenario: Idle state representation
- **WHEN** the daemon is idle and ready for recording
- **THEN** the system tray displays a neutral monochrome microphone icon

#### Scenario: Recording state representation
- **WHEN** recording is toggled on
- **THEN** the system tray icon immediately updates to a distinct active recording visual indicator (such as a red dot or highlighted microphone)

#### Scenario: Headless fallback
- **WHEN** the daemon starts without a graphical session, or on Linux without PyGObject and AppIndicator support
- **THEN** no tray icon is created, the daemon runs headless with unchanged dictation behavior, and an informational notice is logged

#### Scenario: Linux desktop without a tray host
- **WHEN** the daemon starts on a Linux desktop where no StatusNotifierItem host is running (such as GNOME without the AppIndicator extension)
- **THEN** the tray icon is still registered, a notice explains how to enable a tray host, and the icon appears once a host starts

#### Scenario: Problem in the status line
- **WHEN** Vox is idle or paused and something stops dictation
- **THEN** the first menu line reads "Vox Transfer · <problem>", showing only the most urgent one: "Settings file has an error", then "API key needed" or "Can’t read the keyring", then the reason the transcription mode cannot run (for example a missing whisper.cpp model), then "Dictation is off while Settings is open", then a notice such as "Wayland: hotkey and paste only work in X11 apps", "Microphone is silent: check its permission", "Accessibility access needed" or "Last dictation only partly transcribed: see History"
- **AND** a long problem is flattened to one line of at most 72 characters

#### Scenario: State shown while busy
- **WHEN** Vox is recording or processing
- **THEN** the first menu line shows that state instead of any problem

#### Scenario: Wayland session
- **WHEN** Vox starts on Linux in a Wayland session (`XDG_SESSION_TYPE=wayland` or `WAYLAND_DISPLAY` set)
- **THEN** it logs a warning explaining that the hotkey, window detection and paste only reach X11 (XWayland) apps, and the status line shows "Wayland: hotkey and paste only work in X11 apps"

#### Scenario: Settings open
- **WHEN** the Settings window is open and Vox is idle
- **THEN** the first menu line reads "Vox Transfer · Dictation is off while Settings is open", unless the settings file, the API key or the transcription mode has a problem, which is shown instead
- **AND** a notice from the daemon waits until the window closes

### Requirement: Visual Custom Vocabulary and Snippet Management
The system SHALL provide Vocabulary and Snippets pages in Settings to view, add, and remove custom dictionary words and snippet expansions, synchronizing changes to the configuration file. While the configuration file cannot be loaded, the pages SHALL change nothing in it.

#### Scenario: Adding custom vocabulary term
- **WHEN** the user submits a new word on the Vocabulary page
- **THEN** the term is appended to the configuration dictionary and hot-reloaded into the running transcription context

#### Scenario: Adding a snippet expansion
- **WHEN** the user submits a trigger phrase and expansion text on the Snippets page
- **THEN** the snippet mapping is saved to the configuration file and active snippet replacement recognizes the new trigger

#### Scenario: Settings file fails to load
- **WHEN** `config.toml` cannot be parsed or fails validation
- **THEN** the pages say they couldn't read the file, disable adding words and New Snippet, and refuse every change (adding or removing words, and saving or deleting snippets) with a "Couldn't Save" message that names the problem, leaving the file unchanged

## REMOVED Requirements

### Requirement: Audio Device Selection from Tray
**Reason**: The Input Device submenu is replaced by the microphone choice on the General page of Settings, which saves the device to `config.toml` instead of keeping it until Vox quits.
**Migration**: Choose the microphone in Settings > General. See "Microphone Selection in Settings".

### Requirement: Transcription Selection from Tray
**Reason**: The Transcription submenu is replaced by the Transcription page of Settings.
**Migration**: Choose the mode in Settings > Transcription. See "Transcription Selection in Settings".

### Requirement: Hotkey Selection from Tray
**Reason**: The Set Hotkey window becomes the Hotkey page of Settings, which saves each accepted key at once. Its rules for keys, combinations and warnings carry over unchanged.
**Migration**: Set the hotkey in Settings > Hotkey. See "Hotkey Selection in Settings". Suspending the hotkey while the window is open, and applying it when the window closes, now cover the whole Settings window (see "Settings Window").
