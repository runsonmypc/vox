# desktop-frontend Specification

## Purpose
Provides a lightweight, cross-platform system tray and desktop management interface for Vox to display recording status, switch microphones, log and search dictation history, and manage vocabulary and snippets.

## Requirements

### Requirement: System Tray Status Indicator
The system SHALL provide a system tray icon on macOS (menu bar) and Linux (AppIndicator / StatusNotifierItem) that visually reflects the current daemon state. Wherever no graphical session or tray support is available, the daemon SHALL run headless as before.

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

### Requirement: Self-Contained Per-User Installation
The system SHALL provide one install script for macOS and Linux that installs Vox and its tray support for the current user, without keeping the source checkout and without a compiler.

#### Scenario: Fresh install
- **WHEN** the user runs `./install.sh`
- **THEN** Vox is installed into its own virtualenv with a `vox` command, missing system packages are installed, and once an OpenAI API key is configured Vox starts at login and a Vox launcher is added to the system's applications

#### Scenario: Start after Quit
- **WHEN** the user has quit Vox from the tray and opens the Vox launcher (the Applications folder or Spotlight on macOS, the applications list on Linux)
- **THEN** Vox starts again through its login service with the same permissions, and opening the launcher while Vox is running does nothing

#### Scenario: Already running
- **WHEN** Vox is started while another instance is running
- **THEN** it exits successfully before any permission prompt, so the service manager does not retry it

#### Scenario: GNOME tray host
- **WHEN** the installer runs on GNOME and no tray host is present
- **THEN** it installs and enables the AppIndicator extension through GNOME's own confirmation dialog, without sudo or logging out

#### Scenario: Update
- **WHEN** the user runs `./install.sh` again from a newer checkout
- **THEN** the virtualenv is rebuilt from that checkout, existing configuration and history are kept, and the running service is restarted

### Requirement: Audio Device Selection from Tray
The system SHALL list available audio input devices in the tray context menu and allow the user to select an active device at runtime.

#### Scenario: Switching input microphone
- **WHEN** the user selects an audio device from the tray menu
- **THEN** the system updates the active audio input device and applies it to subsequent recording sessions without requiring a daemon restart

### Requirement: Transcription Selection from Tray
The system SHALL let the user choose OpenAI batch, OpenAI streaming, or local whisper.cpp transcription from the tray menu while idle and persist the selection.

#### Scenario: Switching providers
- **WHEN** the user selects an available transcription mode while idle
- **THEN** the next recording uses that mode, the menu marks it selected, and the selection is saved for the next launch

#### Scenario: Unavailable mode
- **WHEN** the OpenAI API key or local whisper.cpp setup required by a mode is missing
- **THEN** that mode is disabled in the menu and the active mode remains selected

#### Scenario: Active recording
- **WHEN** the daemon is recording or processing a dictation
- **THEN** transcription mode choices are disabled until it returns to idle

### Requirement: Pause and Resume Dictation from Tray
The system SHALL allow the user to temporarily pause or resume global hotkey listening from the tray menu.

#### Scenario: Pausing hotkey capture
- **WHEN** the user toggles the pause state from the tray menu
- **THEN** global hotkey capture is suspended and system sounds indicate the paused state

#### Scenario: Resuming hotkey capture
- **WHEN** the user unpauses from the tray menu
- **THEN** global hotkey capture resumes normal recording behavior

### Requirement: Local Dictation History Persistence
The system SHALL automatically persist completed dictations and associated metadata into a local SQLite database upon successful text injection.

#### Scenario: Persisting completed transcript
- **WHEN** a transcription is successfully injected at the user cursor
- **THEN** the system stores the timestamp, transcribed text, active window class/type, and duration in the local history database

#### Scenario: Empty transcription handling
- **WHEN** a recording completes with empty or whitespace-only transcript
- **THEN** no entry is added to the history database

### Requirement: History Search and Clipboard Recovery Drawer
The system SHALL provide a lightweight window or popover allowing users to search past dictations and copy or re-paste selected transcripts.

#### Scenario: Searching past dictations
- **WHEN** the user opens the history interface and enters a search query
- **THEN** the system filters and displays matching historical dictations sorted by timestamp descending

#### Scenario: 1-click clipboard recovery
- **WHEN** the user clicks the copy action on a historical dictation entry
- **THEN** the selected text is copied to the system clipboard

### Requirement: Visual Custom Vocabulary and Snippet Management
The system SHALL provide a management interface to view, add, and remove custom dictionary words and snippet expansions, synchronizing changes to the configuration file.

#### Scenario: Adding custom vocabulary term
- **WHEN** the user submits a new word in the vocabulary manager
- **THEN** the term is appended to the configuration dictionary and immediately hot-reloaded into the running transcription context

#### Scenario: Adding a snippet expansion
- **WHEN** the user submits a trigger phrase and expansion text
- **THEN** the snippet mapping is saved to the configuration file and active snippet replacement recognizes the new trigger
