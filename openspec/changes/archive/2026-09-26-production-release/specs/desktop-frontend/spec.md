## MODIFIED Requirements

### Requirement: System Tray Status Indicator
The system SHALL provide a system tray icon on macOS (menu bar) and Linux (AppIndicator / StatusNotifierItem) that visually reflects the current daemon state. Wherever no graphical session or tray support is available, the daemon SHALL run headless as before. While the daemon is idle or paused, the first menu line SHALL name the most urgent problem that stops dictation, in this order: a settings file that could not be loaded (until it loads, the mode and so the need for a key are only the defaults), a missing or unreadable API key, a transcription mode that cannot run, then a notice from the daemon.

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
- **THEN** the first menu line reads "Vox · <problem>", showing only the most urgent one: "Settings file has an error", then "API key needed" or "Can’t read the keyring", then the reason the transcription mode cannot run (for example a missing whisper.cpp model), then a notice such as "Wayland: hotkey and paste only work in X11 apps", "Microphone is silent: check its permission", "Accessibility access needed" or "Last dictation only partly transcribed: see History"
- **AND** a long problem is flattened to one line of at most 72 characters

#### Scenario: State shown while busy
- **WHEN** Vox is recording or processing
- **THEN** the first menu line shows that state instead of any problem

#### Scenario: Wayland session
- **WHEN** Vox starts on Linux in a Wayland session (`XDG_SESSION_TYPE=wayland` or `WAYLAND_DISPLAY` set)
- **THEN** it logs a warning explaining that the hotkey, window detection and paste only reach X11 (XWayland) apps, and the status line shows "Wayland: hotkey and paste only work in X11 apps"

### Requirement: Audio Device Selection from Tray
The system SHALL list available audio input devices in the tray context menu and allow the user to select an active device at runtime. The menu SHALL be built only from the input-device list the daemon last scanned, never by querying audio devices on the tray's thread, and a selection SHALL be remembered by device name.

#### Scenario: Switching input microphone
- **WHEN** the user selects an audio device from the tray menu
- **THEN** the system updates the active audio input device and applies it to subsequent recording sessions without requiring a daemon restart

#### Scenario: Device connected after start
- **WHEN** a microphone is connected or removed while Vox runs
- **THEN** the Input Device menu shows the change after the daemon's next device rescan

#### Scenario: Before the first scan
- **WHEN** the menu opens before the daemon has sent its first device list
- **THEN** it shows System Default and the configured device, if any

#### Scenario: Selection survives renumbering
- **WHEN** the user picks a device and the audio system later renumbers its devices
- **THEN** Vox still records from the device with that name, and the menu checks that device: an exact name match wins over a name that only contains it, and of two devices with the same name the first is checked, since it is the one that records

#### Scenario: Selection is for this session
- **WHEN** the user picks a device from the menu
- **THEN** the choice lasts until Vox quits and is not written to `config.toml`, and later edits to `config.toml` that do not change `[audio]` do not undo it

### Requirement: Transcription Selection from Tray
The system SHALL let the user choose OpenAI batch, OpenAI streaming, or local whisper.cpp transcription from the tray menu while idle and persist the selection. Whether a mode can be chosen SHALL follow the same rules everywhere Vox checks it: the OpenAI modes need an API key, and the local mode needs a working whisper.cpp binary and model.

#### Scenario: Switching providers
- **WHEN** the user selects an available transcription mode while idle
- **THEN** the next recording uses that mode, the menu marks it selected, and the selection is saved for the next launch

#### Scenario: Unavailable mode
- **WHEN** the OpenAI API key or local whisper.cpp setup required by a mode is missing
- **THEN** that mode is disabled in the menu and the active mode remains selected

#### Scenario: Active recording
- **WHEN** the daemon is recording or processing a dictation
- **THEN** transcription mode choices are disabled until it returns to idle

#### Scenario: Configured mode cannot run
- **WHEN** the configured mode cannot run, for example because the whisper.cpp model file is missing
- **THEN** Vox still starts, the mode stays selected, the status line shows why it cannot run, and choosing another available mode clears the problem

#### Scenario: Switch fails
- **WHEN** the chosen mode cannot be built or the selection cannot be saved
- **THEN** the mode stays as it was, a warning is logged, and the error sound plays

### Requirement: Local Dictation History Persistence
The system SHALL persist every non-empty transcript, and its metadata, into a local SQLite database, including when pasting it fails. The database SHALL be readable only by the user, and dictations deleted from it SHALL be overwritten in the file rather than only unlinked.

#### Scenario: Persisting completed transcript
- **WHEN** a transcription produces text
- **THEN** the system stores the timestamp, transcribed text, the kind of app it went to (not the window title), the recording's duration, and the transcription provider in the local history database

#### Scenario: Paste fails
- **WHEN** transcription succeeds but the paste fails
- **THEN** the error sound plays and the transcript is still stored, so it can be copied from history

#### Scenario: Long recording fails partway
- **WHEN** a recording sent in parts fails after some parts were transcribed
- **THEN** the text of those parts is stored and the error sound plays; when history cannot store it (history unavailable or the save fails), the text is pasted instead of being dropped

#### Scenario: Provider that produced the text
- **WHEN** a streaming recording falls back to batch transcription
- **THEN** the entry records "batch" as its provider, and otherwise the entry records the mode the recording started in, even if the mode was changed during the transcription

#### Scenario: Empty transcription handling
- **WHEN** a recording completes with empty or whitespace-only transcript
- **THEN** no entry is added to the history database

#### Scenario: Private database file
- **WHEN** the history database is created or opened
- **THEN** the file has mode 0600, a directory Vox creates for it has mode 0700, and Vox's own data directory and an older database are tightened to those modes

### Requirement: History Search and Clipboard Recovery Drawer
The system SHALL provide a lightweight window or popover allowing users to search past dictations, copy or re-paste selected transcripts, and delete dictations.

#### Scenario: Searching past dictations
- **WHEN** the user opens the history interface and enters a search query
- **THEN** the system filters and displays matching historical dictations sorted by timestamp descending, matching the query literally and ignoring case for every alphabet (so "über" finds "Über")

#### Scenario: 1-click clipboard recovery
- **WHEN** the user clicks the copy action on a historical dictation entry
- **THEN** the selected text is copied to the system clipboard as a normal copy that clipboard managers see

#### Scenario: Copying a selection
- **WHEN** the user presses Cmd+C (macOS) or Ctrl+C (Linux) in the history window, on any keyboard layout
- **THEN** the words selected in the reading pane are copied, or the whole dictation when nothing is selected

#### Scenario: Deleting a dictation
- **WHEN** the user deletes the dictation being read (the trash button, or Cmd+Delete in the macOS list)
- **THEN** it is removed from the history database without a confirmation, and the next dictation is selected

#### Scenario: Clearing history
- **WHEN** the user chooses Clear History and confirms the dialog
- **THEN** every dictation is deleted, and cancelling the dialog deletes nothing

#### Scenario: Long history
- **WHEN** more than 200 dictations match
- **THEN** the newest 200 are listed with a note under the list saying that searching finds older ones

#### Scenario: Recent list after changes
- **WHEN** the history window closes
- **THEN** the tray re-reads its recent dictations, so deleted ones disappear from the menu

### Requirement: Visual Custom Vocabulary and Snippet Management
The system SHALL provide a management interface to view, add, and remove custom dictionary words and snippet expansions, synchronizing changes to the configuration file. While the configuration file cannot be loaded, the interface SHALL change nothing in it.

#### Scenario: Adding custom vocabulary term
- **WHEN** the user submits a new word in the vocabulary manager
- **THEN** the term is appended to the configuration dictionary and immediately hot-reloaded into the running transcription context

#### Scenario: Adding a snippet expansion
- **WHEN** the user submits a trigger phrase and expansion text
- **THEN** the snippet mapping is saved to the configuration file and active snippet replacement recognizes the new trigger

#### Scenario: Settings file fails to load
- **WHEN** `config.toml` cannot be parsed or fails validation
- **THEN** the vocabulary window says it couldn't read the file, disables adding words and New Snippet, and refuses every change (adding or removing words, and saving or deleting snippets) with a "Couldn't Save" message that names the problem, leaving the file unchanged

## REMOVED Requirements

### Requirement: Self-Contained Per-User Installation
**Reason**: Installation grew into release packaging: a release bootstrap, uninstall, a Debian package, hash-locked dependencies and CI. It now lives in the new `release-packaging` capability, under the same requirement name, with every earlier scenario kept.
**Migration**: See `release-packaging`, Requirement "Self-Contained Per-User Installation", and the requirements next to it for release installs, uninstall and the `.deb`.
