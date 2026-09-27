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
- **THEN** the first menu line reads "Vox Transfer · <problem>", showing only the most urgent one: "Settings file has an error", then "API key needed" or "Can’t read the keyring", then the reason the transcription mode cannot run (for example a missing whisper.cpp model), then a notice such as "Wayland: hotkey and paste only work in X11 apps", "Microphone is silent: check its permission", "Accessibility access needed" or "Last dictation only partly transcribed: see History"
- **AND** a long problem is flattened to one line of at most 72 characters

#### Scenario: State shown while busy
- **WHEN** Vox is recording or processing
- **THEN** the first menu line shows that state instead of any problem

#### Scenario: Wayland session
- **WHEN** Vox starts on Linux in a Wayland session (`XDG_SESSION_TYPE=wayland` or `WAYLAND_DISPLAY` set)
- **THEN** it logs a warning explaining that the hotkey, window detection and paste only reach X11 (XWayland) apps, and the status line shows "Wayland: hotkey and paste only work in X11 apps"
