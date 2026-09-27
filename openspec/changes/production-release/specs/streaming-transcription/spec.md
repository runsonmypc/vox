## MODIFIED Requirements

### Requirement: Streaming WebSocket Audio Transmission
The system SHALL stream captured audio in real time over a WebSocket connection to OpenAI's Realtime transcription endpoint (`wss://api.openai.com/v1/realtime?intent=transcription`) using `gpt-live-transcribe` and 24kHz PCM16 mono format. Audio captured at another rate SHALL be converted to 24 kHz through a band-limiting low-pass filter that keeps its state from one chunk to the next, so that no aliasing and no discontinuity at chunk boundaries reaches the stream.

#### Scenario: Active recording streams chunks continuously
- **WHEN** recording is active in streaming mode
- **THEN** audio chunks are continuously encoded in base64 and appended to the Realtime input audio buffer over the WebSocket connection without waiting for recording to end

#### Scenario: Resilient connection initialization
- **WHEN** streaming transcription is initiated
- **THEN** the system establishes the WebSocket connection with authorization headers and keepalive pings, and initializes the session with transcription configuration

#### Scenario: Capture rate differs from 24 kHz
- **WHEN** the microphone records at 48 kHz or 44.1 kHz
- **THEN** each chunk is low-pass filtered and resampled to 24 kHz mono, continuing from the previous chunk's filter state, so the streamed audio has no click or gap where one chunk ends and the next begins

#### Scenario: Only streaming recordings feed the stream
- **WHEN** a recording is made in batch or whisper.cpp mode
- **THEN** no 24 kHz chunks are produced for it

### Requirement: Transcription Context and Keyword Hints
The system SHALL provide the configured prompt and the filtered vocabulary keywords to the streaming transcription session during initialization. The keywords SHALL come from the same filtered list that batch mode sends, and the session SHALL NOT receive raw window titles or screen text.

#### Scenario: Screen OCR and dictionary terms configured
- **WHEN** a streaming session starts
- **THEN** the configured prompt is sent as the session prompt, and the dictionary terms plus, when `[context] screen` is on, words from the focused window's title are sent as keywords: the same list batch mode builds, with secret-looking tokens removed, at most 40 words, and none containing `<`, `>` or a line break
- **AND** no screen capture runs for the session, since its keywords are sent when it connects

#### Scenario: Screen context off
- **WHEN** `[context] screen = false`
- **THEN** only the dictionary terms are sent as keywords

### Requirement: Single-Shot Paste on Recording Completion
The system SHALL commit the audio buffer upon recording completion, wait for the final completed transcription event, expand configured snippets, and paste the entire accumulated text at the cursor position in a single clipboard operation, after which the user's clipboard is restored.

#### Scenario: Recording stops and text is pasted
- **WHEN** recording ends via key release or toggle
- **THEN** the system commits the audio buffer, receives the final transcription, applies snippet replacements, pastes the full transcript through the clipboard with the platform's paste shortcut (`Cmd+V` on macOS, `Ctrl+V` or `Ctrl+Shift+V` on Linux), and then restores the clipboard

#### Scenario: Empty or silent recording ignored
- **WHEN** the completed transcript is empty or contains only whitespace
- **THEN** the system terminates the turn cleanly without altering the system clipboard or emitting paste keystrokes

#### Scenario: Transcript that only echoes the hints
- **WHEN** the final transcript only repeats most of the session prompt and keywords, as a model can on silence
- **THEN** it is dropped as empty

### Requirement: Configurable Mode and Batch Fallback
The system SHALL support selecting between streaming and batch transcription modes via configuration, and SHALL automatically fall back to batch transcription if the streaming connection fails, closes before the final transcript, or the server reports an error. It SHALL NOT end a streaming session that is still working with a fixed deadline, and SHALL NOT paste a partial transcript as final.

#### Scenario: Automatic fallback on WebSocket connection error
- **WHEN** streaming mode is active but the WebSocket connection fails to connect or disconnects unexpectedly
- **THEN** the system logs a warning and falls back to transcribing the captured audio buffer via the batch transcription endpoint

#### Scenario: Explicit batch mode configuration
- **WHEN** the user configures `mode = "batch"` in configuration
- **THEN** the system bypasses WebSocket streaming and uses the batch transcription API directly

#### Scenario: Waiting for the final transcript
- **WHEN** the audio buffer is committed
- **THEN** the system waits for the server's completed event with no fixed deadline, relying on the WebSocket keepalive pings to detect a dead connection

#### Scenario: Server error before the final transcript
- **WHEN** the Realtime session reports an error event before the final transcript, even while the socket stays open
- **THEN** the system stops sending audio to it, sends no commit, and falls back to batch transcription of the captured audio

#### Scenario: Screen hints for the fallback
- **WHEN** a streaming recording falls back to batch and `[context] screen` is on
- **THEN** the focused window is captured at that point and its filtered words are added to the batch request's hints

#### Scenario: Error after completion
- **WHEN** the server reports an error or the connection drops after the final transcript arrived
- **THEN** the final transcript is still used and no fallback runs
