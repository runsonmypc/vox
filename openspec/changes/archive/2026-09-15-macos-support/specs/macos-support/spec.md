## Purpose

Enables vox to operate natively on macOS Darwin, providing voice-to-text dictation with active application context, screen vocabulary extraction, audio attenuation, and seamless background execution.

## ADDED Requirements

### Requirement: Cross-Platform Single-Instance Locking
The daemon SHALL ensure that only one instance runs at a time using an advisory file lock mechanism that functions portably across macOS and Linux without relying on Linux-only abstract Unix domain sockets.

#### Scenario: First instance starts successfully
- **WHEN** no other vox instance is running
- **THEN** the daemon acquires the lock file and proceeds with initialization

#### Scenario: Second instance is prevented from starting
- **WHEN** a vox instance is already active and running
- **THEN** a newly launched instance fails to acquire the lock, logs an error message indicating another instance is running, and exits with code 1

### Requirement: macOS Active Application and Window Detection
On macOS, the daemon SHALL determine the frontmost application, its bundle identifier, its process ID, and its window title using native macOS APIs instead of X11 utilities (`xdotool`, `xprop`).

#### Scenario: Active app is classified correctly
- **WHEN** recording is initiated while a known terminal, editor, chat, browser, or email client is focused on macOS
- **THEN** the daemon resolves the application bundle identifier and maps it to the appropriate `AppType` (e.g., `TERMINAL`, `EDITOR`, `CHAT`, `BROWSER`, `EMAIL`)

#### Scenario: Window title fallback when unavailable
- **WHEN** an application window title cannot be inspected due to privacy restrictions or absence of a window
- **THEN** the daemon falls back gracefully to the application localized name without raising an unhandled exception

### Requirement: macOS Text Injection and Clipboard Handling
On macOS, the daemon SHALL insert transcribed text at the cursor position by updating the system pasteboard via native pasteboard APIs and issuing a Command+V (`⌘V`) key combination to the active application.

#### Scenario: Text injection into GUI application
- **WHEN** transcription completes for a focused GUI application (editor, browser, chat)
- **THEN** the daemon saves the existing clipboard contents, places the transcribed text on the pasteboard, simulates Command+V, and restores the original clipboard contents

#### Scenario: Text injection into terminal application
- **WHEN** transcription completes for a focused macOS terminal application (Ghostty, iTerm2, Terminal.app)
- **THEN** the daemon simulates Command+V (`⌘V`) rather than Linux-style Control+Shift+V

### Requirement: macOS Audio Volume Attenuation
When attenuation is enabled on macOS, the daemon SHALL query the current output volume and attenuate system output volume while recording, restoring the original volume level upon completion.

#### Scenario: Volume attenuated during recording
- **WHEN** audio recording starts with attenuation enabled
- **THEN** the current macOS system volume is saved and output volume is reduced to the configured attenuation level

#### Scenario: Volume restored when recording stops
- **WHEN** audio recording stops or errors out
- **THEN** the system volume is restored to its original pre-recording level

### Requirement: macOS Screen Context Capture
On macOS, the daemon SHALL support capturing visible screen content for vocabulary and technical term extraction during recording using native macOS screen capture and on-device text recognition.

#### Scenario: Neural OCR extracts vocabulary hints
- **WHEN** recording begins and screen capture is triggered
- **THEN** the screen is captured and processed through Apple Vision text recognition to extract unique vocabulary words into the transcription prompt

#### Scenario: Terminal tmux pane extraction
- **WHEN** the active application is a terminal running tmux
- **THEN** the daemon captures the visible tmux pane contents for context hints

### Requirement: macOS System Permissions Diagnostics
On startup on macOS, the daemon SHALL verify necessary OS permissions (Accessibility and Microphone) and provide actionable instructions if any required permission is missing.

#### Scenario: Accessibility permission missing
- **WHEN** vox starts on macOS without Accessibility permissions granted to the host process
- **THEN** the daemon logs a prominent warning directing the user to enable Accessibility in System Settings

### Requirement: macOS Background Daemon LaunchAgent
The project SHALL provide a macOS LaunchAgent property list definition allowing vox to be started, stopped, and managed via `launchctl` as a persistent background daemon for the user session.

#### Scenario: Daemon starts via launchd
- **WHEN** the LaunchAgent is loaded via `launchctl bootstrap` or `launchctl load`
- **THEN** the vox daemon starts in the background and restarts automatically on failure
