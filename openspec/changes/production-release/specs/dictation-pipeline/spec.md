## Purpose

Defines what happens to a dictation from the hotkey to the pasted text in every transcription mode: how long a recording may run, how its audio is converted and uploaded, how requests and failures are handled without losing billed work, how the paste restores the clipboard, what the logs may contain, and what stops dictation while a setting, the microphone or an input device needs attention.

## ADDED Requirements

### Requirement: Recording Limit Auto-Stop
The system SHALL stop a recording by itself when it reaches `[audio] max_recording_seconds` (default 900 seconds) and SHALL transcribe and paste it exactly as if the user had stopped it. The audio held for one recording SHALL never exceed that limit.

#### Scenario: Limit reached
- **WHEN** a recording reaches the limit
- **THEN** the recorder stops keeping audio at exactly the limit, the recording stops with the normal stop sound, is transcribed and pasted, and the log says "Recording limit reached (N s)" at INFO

#### Scenario: Limit changed during a recording
- **WHEN** the limit changes while a recording runs
- **THEN** that recording keeps the limit it started with, and the new limit applies from the next recording

#### Scenario: Stale limit event
- **WHEN** a limit event arrives after its recording was cancelled or already stopped
- **THEN** it is ignored and does not stop a newer recording

### Requirement: Recording Limit from the Tray
The system SHALL offer a Recording Limit submenu in the tray with 5, 10, 15, 30 and 60 minutes, checked on the current limit, and SHALL save a choice to `[audio] max_recording_seconds`.

#### Scenario: Picking a limit
- **WHEN** the user picks a limit from the submenu
- **THEN** it is saved to `[audio] max_recording_seconds` with the rest of `config.toml` and its comments kept, the menu checks it, and it applies from the next recording

#### Scenario: Custom limit from the file
- **WHEN** `config.toml` sets a limit that is not one of the choices
- **THEN** the submenu also lists that value, checked

#### Scenario: Limit cannot be saved
- **WHEN** the chosen limit cannot be written to `config.toml`
- **THEN** a warning is logged, the error sound plays, and the limit stays as it was

### Requirement: Batch Upload Format
The system SHALL convert a finished recording to 16 kHz mono 16-bit PCM WAV before sending it to OpenAI's batch transcription or to whisper.cpp, and SHALL do the conversion in blocks, so memory stays bounded however long the recording is.

#### Scenario: Batch upload
- **WHEN** a recording made at 48 kHz, 44.1 kHz or with several channels is sent for batch transcription
- **THEN** OpenAI receives a 16 kHz mono PCM16 WAV file

#### Scenario: Local transcription input
- **WHEN** a recording is transcribed with whisper.cpp
- **THEN** whisper.cpp receives a 16 kHz mono PCM16 WAV file

### Requirement: Band-Limited Resampling
Every path that lowers the sample rate, which covers the batch upload, the speech detector, the whisper.cpp input and the 24 kHz streaming audio, SHALL low-pass filter the audio before decimating it, so frequencies above the new rate's limit do not fold back into the speech band. Whole-number ratios such as 48 kHz to 16 kHz or 24 kHz SHALL use a direct decimation path, and other ratios such as 44.1 kHz SHALL low-pass and then interpolate.

#### Scenario: High-frequency content
- **WHEN** a 48 kHz recording contains sound above 8 kHz and is converted to 16 kHz
- **THEN** that sound is attenuated by the filter instead of reappearing as a lower-pitched alias

#### Scenario: 44.1 kHz input device
- **WHEN** the input device records at 44.1 kHz
- **THEN** speech detection, the 16 kHz upload, the whisper.cpp input and the 24 kHz stream all work from it

### Requirement: Long Recordings Sent in Parts
When a recording's upload file would exceed about 24 MB (roughly 13 minutes at 16 kHz), the system SHALL split it at pauses into parts under that size, transcribe the parts one after another in order, and join their texts with a single space.

#### Scenario: Long recording
- **WHEN** a recording's 16 kHz upload would be larger than the limit
- **THEN** it is cut, for each part, in the quietest 300 ms of that part's last quarter, the parts are transcribed in order, and the pasted text is their transcripts joined with a space

#### Scenario: Part that only echoes the hints
- **WHEN** one part's transcript only repeats the hints
- **THEN** that part is left out and the others are joined

#### Scenario: A part fails
- **WHEN** a part fails after earlier parts were transcribed
- **THEN** no later part is sent, the text transcribed so far is saved to history (not pasted), the error sound plays, and the tray shows "Last dictation only partly transcribed: see History" until the next successful dictation

#### Scenario: The first part fails
- **WHEN** the first part fails
- **THEN** the error sound plays and nothing is saved

### Requirement: Transcription Requests
The system SHALL NOT throw away transcription work that OpenAI has already been asked to do, except when the user cancels it. It SHALL send each upload once and let the OpenAI SDK retry transient failures under its own policy, and SHALL NOT impose a deadline shorter than the SDK's own timeouts.

#### Scenario: Transient failure
- **WHEN** a batch request fails with an error the SDK retries
- **THEN** only the SDK's retries run, and Vox adds no retry loop of its own on top of them

#### Scenario: Request rejected
- **WHEN** a batch request fails with an error the SDK does not retry, such as a rejected key or an invalid request
- **THEN** the dictation fails at once with the error sound and a log line, without further attempts

#### Scenario: No key
- **WHEN** an OpenAI mode has no API key at transcription time
- **THEN** it fails without calling OpenAI

### Requirement: Pasting the Transcript
The system SHALL paste the transcript into the focused app through the clipboard, one paste at a time, without blocking the daemon's event handling. On Linux it SHALL choose the paste shortcut from the window that has focus at paste time. Every local tool it runs SHALL have a timeout.

#### Scenario: Focus moved during transcription (Linux)
- **WHEN** the user switches from an editor to a terminal while the dictation is being transcribed
- **THEN** the paste uses the terminal's shortcut (`Ctrl+Shift+V`), and when the window at paste time cannot be identified, the shortcut for the window focused when recording stopped is used

#### Scenario: Linux paste shortcuts
- **WHEN** Vox pastes on Linux
- **THEN** terminals get `Ctrl+Shift+V`, apps it does not classify get `Ctrl+V` through XTest, and other apps get `Ctrl+V` through `xdotool`

#### Scenario: Clipboard cannot be set
- **WHEN** the transcript cannot be put on the clipboard, for example because `xclip` is missing or fails
- **THEN** no paste keystroke is sent, so the old clipboard is never pasted in its place, and the paste counts as failed

#### Scenario: Paste fails
- **WHEN** pasting fails for any reason
- **THEN** the error is logged, the error sound plays, the transcript is still saved to history, and the recording is not transcribed again

### Requirement: Clipboard Restore After Paste
After pasting, the system SHALL restore what was on the clipboard before, for every kind of app. Vox's temporary dictation write SHALL be hidden from clipboard history where the platform allows it, while an explicit Copy by the user SHALL stay a normal copy.

#### Scenario: Restore on macOS
- **WHEN** a paste finishes on macOS
- **THEN** every item on the previous clipboard comes back with every type it offered, including images, copied files and rich text, after a wait of 0.5 seconds for the target app to read the dictation

#### Scenario: Restore on Linux (X11)
- **WHEN** a paste finishes on Linux
- **THEN** the previous clipboard comes back in its most useful single form (copied files as a file list, otherwise plain text, otherwise a PNG image or HTML, otherwise another image type, otherwise a list of links), once the target has fetched the dictation or after about one second if it never asks

#### Scenario: Copy during the paste
- **WHEN** the user or another app copies something while a paste is finishing
- **THEN** the newer clipboard contents are kept and not overwritten with the old ones

#### Scenario: Empty clipboard
- **WHEN** the clipboard was empty before the paste
- **THEN** it is empty again afterwards, so the dictation does not linger on it

#### Scenario: Temporary dictation on macOS
- **WHEN** Vox puts a dictation on the macOS clipboard only to paste it
- **THEN** the write is marked `org.nspasteboard.TransientType` and `org.nspasteboard.AutoGeneratedType` and kept to this Mac, so clipboard managers and Universal Clipboard ignore it

#### Scenario: Explicit Copy
- **WHEN** the user copies a dictation from the tray's recent list or the history window
- **THEN** it is a normal clipboard copy that clipboard managers and Universal Clipboard see

### Requirement: Logging Without Dictated Text
The system SHALL keep what the user said out of its normal logs. At INFO and above, Vox's log messages SHALL record events, lengths, modes, timings and errors, and SHALL NOT contain transcript text or snippet expansions. That text SHALL appear only at DEBUG level (`vox -v`), as do the window titles Vox detects.

#### Scenario: Normal logging
- **WHEN** a dictation is transcribed and pasted with default logging
- **THEN** the log shows its length in characters, for example "Transcript: 42 chars", and not its text

#### Scenario: Snippet expansion
- **WHEN** a dictation matches a snippet trigger
- **THEN** the INFO log says a snippet was expanded and gives its length, and the trigger and expansion appear only at DEBUG

#### Scenario: Verbose logging
- **WHEN** Vox runs with `-v`
- **THEN** transcripts, dropped echoes, snippet matches and window titles are logged at DEBUG

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
- **THEN** within a few seconds Vox applies it, clears the error, and updates the status line, with no restart; settings that always need a restart, such as `[hotkey]`, apply after the next restart

#### Scenario: Permanent startup failure
- **WHEN** a required system tool is missing (for example `xdotool` or `xclip` on Linux) or the lock directory cannot be created
- **THEN** Vox logs what is missing and exits with status 78, which the Linux login service does not retry

### Requirement: Settings Reload
The system SHALL apply edits to `config.toml` while running, a few seconds after a save, except for settings that need a restart.

#### Scenario: Hot-reloaded settings
- **WHEN** the user changes the dictionary, snippets, window classes, screen hints, sounds, attenuation, the recording limit, the transcription mode, a model, the language or the prompt
- **THEN** the next dictation uses the new values without a restart

#### Scenario: Audio settings during a recording
- **WHEN** `[audio]` device, sample rate or channels change while a recording runs
- **THEN** the change is applied once that recording ends, and the recording is not lost

#### Scenario: Hotkey settings
- **WHEN** `[hotkey]` changes
- **THEN** the change applies after Vox restarts

#### Scenario: Invalid edit while running
- **WHEN** a `config.toml` that loaded is edited into an invalid state while Vox runs
- **THEN** Vox logs a warning naming the file and the setting and keeps the settings it has

### Requirement: Transcription Mode Problems
The system SHALL start even when the configured transcription mode cannot run, such as whisper.cpp with a missing binary or model, and SHALL retry the mode's setup on every hotkey press, so fixing it needs no restart.

#### Scenario: Mode cannot run at startup
- **WHEN** Vox starts in a mode whose setup is broken
- **THEN** it logs the error, keeps running, and shows the problem in the tray's status line

#### Scenario: Hotkey while the mode cannot run
- **WHEN** the user presses the hotkey and the mode's setup is still broken
- **THEN** the error sound plays, the reason is logged, and no recording starts

#### Scenario: Setup fixed
- **WHEN** the user fixes the setup (for example by adding the model file) and presses the hotkey
- **THEN** the problem clears, the status line updates, and the recording starts

#### Scenario: Another mode chosen
- **WHEN** the user switches to a mode that can run
- **THEN** the problem clears

### Requirement: Silent Microphone Detection
The system SHALL treat a recording whose samples are all exactly zero as a microphone that Vox is not allowed to use, not as silence, and SHALL tell the user.

#### Scenario: All-zero recording
- **WHEN** a recording's samples are all exactly zero
- **THEN** it is not transcribed, a warning explains that the microphone may be blocked (on macOS: System Settings > Privacy & Security > Microphone), the error sound plays, and the tray shows "Microphone is silent: check its permission"

#### Scenario: Real audio again
- **WHEN** a later recording has real audio
- **THEN** the notice is cleared

### Requirement: Input Device Rescan
The system SHALL rescan the audio input devices so the tray can offer devices connected after Vox started, without disturbing a recording or delaying the hotkey.

#### Scenario: Startup and return to idle
- **WHEN** Vox starts, or returns to idle after a dictation (after a short delay so its sounds finish)
- **THEN** it re-initialises the audio system, lists the input devices, and sends the list to the tray only if it differs from the last one sent

#### Scenario: Periodic rescan on macOS
- **WHEN** Vox stays idle on macOS
- **THEN** it also rescans every 30 seconds, while on Linux, where the default PipeWire or PulseAudio device already follows hotplugged devices, it rescans only on returning to idle

#### Scenario: Never during a recording
- **WHEN** an audio stream is open
- **THEN** no rescan re-initialises the audio system

#### Scenario: Hotkey during a rescan
- **WHEN** the hotkey is pressed while a rescan is pending or running
- **THEN** a pending rescan is dropped, and the hotkey waits at most for a running rescan to finish

### Requirement: Hotkey Names
The system SHALL accept the hotkey names users naturally write and SHALL match them to the key that was actually pressed. Without a usable keyboard backend it SHALL exit with a clear message.

#### Scenario: Spellings
- **WHEN** `[hotkey] key` is `right_shift`, `"Right Shift"`, `right_ctrl`, `right_alt`, `left_shift`, `left_ctrl` or `left_alt`, or `fallback` is a combination such as `"left_ctrl+space"`
- **THEN** each names the intended key

#### Scenario: Left and right modifiers
- **WHEN** the hotkey is `left_shift`
- **THEN** it fires on the left Shift key and not on the right one

#### Scenario: No keyboard backend
- **WHEN** global hotkeys cannot be set up, for example with no X11 display on Linux
- **THEN** Vox logs that global hotkeys are unavailable, with the reason, and that it needs an X11 display, and exits with status 1 instead of a traceback

### Requirement: Audio Feedback Sounds
The system SHALL play a short sound for start, stop, error, busy, cancel, pause and resume while `[sounds] enabled` is on, SHALL follow changes to that setting without a restart, and SHALL let the user replace any of these sounds.

#### Scenario: Built-in sounds
- **WHEN** no custom sound is installed
- **THEN** macOS plays its system alert sounds and Linux plays short synthetic tones

#### Scenario: Custom sound
- **WHEN** `~/.config/vox/sounds/<name>.wav` exists for one of the sound names (`start`, `stop`, `error`, `busy`, `cancel`, `pause`, `resume`)
- **THEN** Vox plays that file instead of the built-in sound

#### Scenario: Sounds turned off
- **WHEN** `[sounds] enabled = false`, including after an edit while Vox runs
- **THEN** no sound plays
