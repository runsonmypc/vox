## Purpose

Provides a lightweight, cross-platform system tray and desktop management interface for Vox to display recording status, switch microphones, log and search dictation history, and manage vocabulary and snippets.

## ADDED Requirements

### Requirement: System Tray Status Indicator
The system SHALL provide a cross-platform system tray icon on macOS and Linux that visually reflects the current daemon state.

#### Scenario: Idle state representation
- **WHEN** the daemon is idle and ready for recording
- **THEN** the system tray displays a neutral monochrome microphone icon

#### Scenario: Recording state representation
- **WHEN** recording is toggled on
- **THEN** the system tray icon immediately updates to a distinct active recording visual indicator (such as a red dot or highlighted microphone)

### Requirement: Audio Device Selection from Tray
The system SHALL list available audio input devices in the tray context menu and allow the user to select an active device at runtime.

#### Scenario: Switching input microphone
- **WHEN** the user selects an audio device from the tray menu
- **THEN** the system updates the active audio input device and applies it to subsequent recording sessions without requiring a daemon restart

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
