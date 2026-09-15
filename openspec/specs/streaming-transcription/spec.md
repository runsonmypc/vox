## Purpose

Provides low-latency speech-to-text dictation by streaming audio chunks in real time over WebSockets to OpenAI's `gpt-live-transcribe`, accumulating deltas in memory, and pasting the complete transcript at once upon speech completion.

## Requirements

### Requirement: Streaming WebSocket Audio Transmission
The system SHALL stream captured audio in real time over a WebSocket connection to OpenAI's Realtime transcription endpoint (`wss://api.openai.com/v1/realtime?intent=transcription`) using `gpt-live-transcribe` and 24kHz PCM16 mono format.

#### Scenario: Active recording streams chunks continuously
- **WHEN** recording is active in streaming mode
- **THEN** audio chunks are continuously encoded in base64 and appended to the Realtime input audio buffer over the WebSocket connection without waiting for recording to end

#### Scenario: Resilient connection initialization
- **WHEN** streaming transcription is initiated
- **THEN** the system establishes the WebSocket connection with authorization headers and initializes the session with transcription configuration

### Requirement: Transcription Context and Keyword Hints
The system SHALL provide contextual prompt hints and custom vocabulary keywords to the streaming transcription session during initialization.

#### Scenario: Screen OCR and dictionary terms configured
- **WHEN** a streaming session starts
- **THEN** user-configured dictionary terms, active application title, and screen context are supplied to the session configuration prompt and keywords

### Requirement: In-Memory Transcript Accumulation
The system SHALL accumulate partial transcript delta events received over the WebSocket connection in memory without injecting characters into the target application while recording is active.

#### Scenario: Transcript deltas received during speech
- **WHEN** partial transcript delta events arrive from the streaming service during an active recording session
- **THEN** the system appends the delta tokens to an in-memory transcript buffer and suppresses any keyboard or clipboard injection

### Requirement: Single-Shot Paste on Recording Completion
The system SHALL commit the audio buffer upon recording completion, wait for the final completed transcription event, expand configured snippets, and paste the entire accumulated text at the cursor position in a single clipboard operation.

#### Scenario: Recording stops and text is pasted
- **WHEN** recording ends via key release or toggle
- **THEN** the system commits the audio buffer, receives the final transcription, applies snippet replacements, and injects the full transcript via the system pasteboard and `Cmd+V`

#### Scenario: Empty or silent recording ignored
- **WHEN** the completed transcript is empty or contains only whitespace
- **THEN** the system terminates the turn cleanly without altering the system clipboard or emitting paste keystrokes

### Requirement: Configurable Mode and Batch Fallback
The system SHALL support selecting between streaming and batch transcription modes via configuration, and SHALL automatically fall back to batch transcription if the streaming connection fails or encounters an unrecoverable network error.

#### Scenario: Automatic fallback on WebSocket connection error
- **WHEN** streaming mode is active but the WebSocket connection fails to connect or disconnects unexpectedly
- **THEN** the system logs a warning and falls back to transcribing the captured audio buffer via the batch transcription endpoint

#### Scenario: Explicit batch mode configuration
- **WHEN** the user configures `mode = "batch"` in configuration
- **THEN** the system bypasses WebSocket streaming and uses the batch transcription API directly
