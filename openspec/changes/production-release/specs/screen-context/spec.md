## Purpose

Screen hints help the transcriber spell names and terms that are visible on screen. This capability covers the `[context] screen` setting, what Vox reads from the focused window and when, how secrets are removed before anything leaves the computer, and exactly which words each transcription mode sends to OpenAI.

## ADDED Requirements

### Requirement: Screen Hints Setting
The system SHALL provide a `[context] screen` setting, on by default, that controls whether window-title words and focused-window text are used as transcription hints. A change to the setting SHALL apply from the next dictation without a restart.

#### Scenario: Default
- **WHEN** `config.toml` does not set `[context] screen`
- **THEN** screen hints are on

#### Scenario: Turned off
- **WHEN** `[context] screen = false`
- **THEN** no window-title words and no screen, AT-SPI, tmux or OCR text go to any provider, and no screen capture runs at all, so macOS never asks for the Screen Recording permission

#### Scenario: Paste shortcut still chosen
- **WHEN** screen hints are off
- **THEN** Vox still detects, on the computer, which kind of app has focus, so it can pick the right paste shortcut

#### Scenario: Changed while running
- **WHEN** the user edits `[context] screen` in `config.toml` while Vox runs
- **THEN** the next dictation follows the new value

### Requirement: Focused Window Capture
When screen hints are on, the system SHALL read text from the focused window only, never the whole display and never the focused application's other windows. Text recognition SHALL run on the computer, screenshots SHALL never be kept (on macOS a temporary file deleted as soon as it is read, on Linux an image piped from `maim` to `tesseract` without touching the disk), and at most 2,000 characters SHALL be kept from one capture.

#### Scenario: Linux capture
- **WHEN** a capture runs on Linux
- **THEN** Vox reads, through AT-SPI, only the focused application's active window and only its showing elements; if that yields little text, it screenshots only the focused X window with `maim` and pipes the image to `tesseract` (when both are installed) without a shell or a file; and for a terminal it may use the visible tmux pane

#### Scenario: macOS capture
- **WHEN** a capture runs on macOS
- **THEN** it follows the macos-support requirement "macOS Screen Context Capture": the tmux pane of the focused terminal, or the focused window by its window ID recognized with Apple Vision

#### Scenario: tmux in the focused terminal only
- **WHEN** the focused app is a terminal
- **THEN** a tmux pane is read only if it belongs to a tmux client running inside that terminal's process tree and is the only such pane, and a detached or background session is never read

#### Scenario: Capture fails or finds nothing
- **WHEN** capture fails, times out or finds no text
- **THEN** the dictation is transcribed and pasted without screen words

### Requirement: When Capture Runs
The system SHALL capture the focused window only for recordings whose hints can still use it, and SHALL NOT delay a dictation for long waiting for a capture.

#### Scenario: Batch or local recording
- **WHEN** screen hints are on and a batch or whisper.cpp recording starts
- **THEN** the focused window is captured in the background while the user speaks, and after the recording stops Vox waits at most 1.5 seconds for that capture before transcribing without it

#### Scenario: Streaming recording
- **WHEN** a streaming recording starts
- **THEN** no capture runs, because the session's keywords are sent when it connects, before any capture could finish

#### Scenario: Streaming falls back to batch
- **WHEN** screen hints are on and a streaming recording falls back to batch transcription
- **THEN** the window that was focused when the recording started, found again by the window ID and process detected then, is captured at that point for the batch request's hints; Vox does not look up which window has focus at fallback time

#### Scenario: Cancelled recording
- **WHEN** a recording is cancelled, or stops without anything to transcribe
- **THEN** its capture is dropped and its text is never used

### Requirement: Secret Filtering
Before any window-title or screen text becomes a hint, the system SHALL remove secret-looking text, so that API keys, tokens and passwords on screen are never sent to a provider or passed to a local transcriber.

#### Scenario: Known credential formats
- **WHEN** the focused window shows tokens such as `sk-…` or `sk-proj-…`, `ghp_…`, `gho_…`, `github_pat_…`, `xoxb-…` (and the other Slack `xox[abprs]-` prefixes), `AKIA…` or `ASIA…`, `AIza…`, or a JSON web token starting with `eyJ`
- **THEN** none of those tokens is used as a hint

#### Scenario: Assigned secrets
- **WHEN** the window shows a value after a name containing KEY, TOKEN, SECRET or PASSWORD followed by `=` or `:` (for example `API_KEY=…`, `password: …` or `"token": "…"`)
- **THEN** the value is removed, whatever it looks like

#### Scenario: Keys and random strings
- **WHEN** the window shows a PEM or OpenSSH private-key block, any run of 20 or more base64 or base64url characters that mixes letters and digits (such as an AWS secret key, a hex token or a UUID), or a word of 20 or more characters that mixes upper case, lower case and digits
- **THEN** none of it is used as a hint, including the key block's shorter last line

#### Scenario: Ordinary words kept
- **WHEN** the window shows long words without digits, or dashed or dotted technical names
- **THEN** they can still be used as hints

### Requirement: Hints Sent per Mode
The system SHALL build one hint list for every mode: the dictionary words, then, when screen hints are on, up to 10 words from the focused window's title and up to 25 words from its text, taking only technical terms and capitalized words and skipping common and menu words, without duplicates (ignoring case), and at most 40 words in total. Only this list, the configured prompt and the configured language SHALL accompany the audio, and no raw window title or screen text SHALL be sent.

#### Scenario: OpenAI batch
- **WHEN** a batch dictation is transcribed with `gpt-transcribe`
- **THEN** OpenAI receives the audio, the model name, the configured language and prompt, and the hint list as keywords, leaving out any word containing `<`, `>` or a line break

#### Scenario: OpenAI batch with another model
- **WHEN** a batch dictation uses a model that takes only a prompt
- **THEN** the configured prompt and the hint list are sent as one prompt

#### Scenario: OpenAI streaming
- **WHEN** a streaming session starts
- **THEN** the session receives the configured prompt, the language, and keywords built the same way as batch, which, with no capture at the start, hold only the dictionary words and the title words

#### Scenario: Local whisper.cpp
- **WHEN** a dictation is transcribed with whisper.cpp
- **THEN** nothing is sent to any provider, and the prompt with the hint list is passed only to the local whisper.cpp process

#### Scenario: Screen hints off
- **WHEN** `[context] screen = false`
- **THEN** every mode sends only the dictionary words as hints

### Requirement: Hint Echo Guard
The system SHALL drop a transcript that only repeats the hints, which a model can produce on silence, and SHALL keep a transcript in which the user dictates a few of the words on screen.

#### Scenario: Echo of the hints
- **WHEN** a transcript consists of the prompt, or mostly of hint words that cover at least 70% of the hint list
- **THEN** it is treated as empty and nothing is pasted

#### Scenario: Dictating on-screen names
- **WHEN** a transcript uses a few of the hint words in ordinary speech
- **THEN** it is pasted as usual
