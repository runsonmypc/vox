## MODIFIED Requirements

### Requirement: Long Recordings Sent in Parts
When a recording's upload file would exceed about 24 MB (roughly 13 minutes at 16 kHz), the system SHALL split it at pauses into parts under that size, transcribe the parts one after another in order, and join their texts with a single space. It SHALL NOT upload a part in which nothing is audible, and SHALL NOT lose the text of parts already transcribed when a later part fails.

#### Scenario: Long recording
- **WHEN** a recording's 16 kHz upload would be larger than the limit
- **THEN** it is cut, for each part, in the quietest 300 ms of that part's last quarter, the parts are transcribed in order, and the pasted text is their transcripts joined with a space

#### Scenario: Part that only echoes the hints
- **WHEN** one part's transcript only repeats the hints
- **THEN** that part is left out and the others are joined

#### Scenario: A silent part
- **WHEN** a recording is sent in more than one part and no second of a part is louder than the silence threshold (the energy floor of the speech check, with no voice detection), such as the quiet tail of a recording left running until the limit
- **THEN** that part is not uploaded, the other parts are transcribed and joined, and the log says how many parts were skipped; a part with even a few quiet words in it is still uploaded, and a recording sent as one part is not checked by this rule

#### Scenario: A part fails
- **WHEN** a part fails after earlier parts were transcribed
- **THEN** no later part is sent and the error sound plays
- **AND** partial text is saved to History without pasting; when recovery is enabled, the complete audio is also retained and associated with that entry when storage succeeds
- **AND** a successful text save shows "Last dictation only partly transcribed: see History" until the next successful dictation or recovery; if text cannot be saved it is pasted instead and no History notice is shown
- **AND** an audio or index save failure is reported separately without discarding saved partial text

#### Scenario: The first part fails
- **WHEN** the first part fails
- **THEN** the error sound plays; when recovery is enabled the complete recording is retained locally in a failed History entry for retry, with audio and index save failures distinguished; when recovery is disabled nothing is saved

## ADDED Requirements

### Requirement: Terminal Transcription Failure Recovery
When failed-transcription recovery is enabled, the system SHALL retain the complete captured recording for retry when a transcription attempt fails with an error, including local failures and failure of batch fallback after streaming. It SHALL preserve existing partial-text recovery behavior when recovery storage is unavailable. It SHALL NOT retain audio solely because paste failed, speech was not detected, the transcript was empty, or the user cancelled the initial dictation.

#### Scenario: Local or batch fails
- **WHEN** a finished recording fails local or batch transcription with recovery enabled
- **THEN** the complete recording is saved locally with a failed History entry and the error sound plays

#### Scenario: Streaming fallback also fails
- **WHEN** streaming transcription fails and its automatic batch fallback fails with recovery enabled
- **THEN** the complete recording is retained once for local or batch retry

#### Scenario: Streaming fallback succeeds
- **WHEN** streaming fails but its batch fallback succeeds
- **THEN** the usual transcript and paste are produced without retaining recovery audio

#### Scenario: Partial text when storage is unavailable
- **WHEN** a partial transcription cannot be saved to History
- **THEN** the existing fallback pastes the partial text and Vox reports any failure to retain the audio when recovery is enabled

#### Scenario: Cancellation or successful transcription
- **WHEN** the initial dictation is cancelled, contains no detected speech, returns an empty transcript without an error, or succeeds but cannot paste
- **THEN** no failed recording is created for that reason and existing transcript recovery rules still apply
