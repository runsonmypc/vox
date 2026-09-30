## MODIFIED Requirements

### Requirement: Local Dictation History Persistence
The system SHALL persist every non-empty transcript, and its metadata, into a local SQLite database, including when pasting it fails. When recovery is enabled, it SHALL also persist failed recording entries with empty or partial text, status, a safe error summary, and an association to retained audio when available. The database SHALL be readable only by the user, and dictations deleted from it SHALL be overwritten in the file rather than only unlinked.

#### Scenario: Persisting completed transcript
- **WHEN** a transcription produces text
- **THEN** the system stores the timestamp, transcribed text, the kind of app it went to (not the window title), the recording's duration, and the transcription provider in the local history database

#### Scenario: Paste fails
- **WHEN** transcription succeeds but the paste fails
- **THEN** the error sound plays and the transcript is still stored, so it can be copied from history

#### Scenario: Long recording fails partway
- **WHEN** a recording sent in parts fails after some parts were transcribed
- **THEN** the text of those parts is stored and the error sound plays; when recovery is enabled the entry is linked to the complete retained audio; when history cannot store it (history unavailable or the save fails), the text is pasted instead of being dropped

#### Scenario: Provider that produced the text
- **WHEN** a streaming recording falls back to batch transcription
- **THEN** the entry records "batch" as its provider, and otherwise the entry records the mode the recording started in, even if the mode was changed during the transcription

#### Scenario: Empty transcription handling
- **WHEN** an initial transcription completes without an error with empty or whitespace-only transcript
- **THEN** no entry is added to the history database

#### Scenario: Failure without text
- **WHEN** transcription fails with an error before producing text while recovery is enabled
- **THEN** a failed recording entry is stored with empty text and a retained audio association when saving succeeds

#### Scenario: Retry provider attribution
- **WHEN** a saved recording is successfully retried with a different method
- **THEN** the same entry records the method that produced the recovered text and keeps the original recording metadata

#### Scenario: Private database file
- **WHEN** the history database is created or opened
- **THEN** the file has mode 0600, a directory Vox creates for it has mode 0700, and Vox's own data directory and an older database are tightened to those modes

## ADDED Requirements

### Requirement: Failed Recordings in History
On macOS and Linux, History SHALL list failed recordings alongside completed dictations in timestamp order. It SHALL label failures and partial transcripts, show time, duration, attempted method, and a safe error summary, and offer explicit Local and OpenAI Batch retry actions for retained audio. It SHALL explain unavailable actions and show retry progress and results. It SHALL allow failed entries to be deleted and included in Clear History. Empty failures SHALL be visible with an empty search; text search SHALL continue to match transcript text literally and case-insensitively.

#### Scenario: Failure with no text
- **WHEN** History opens after a failed recording entry was saved without transcript text
- **THEN** the entry is visible as a failed recording, with recovery controls and Copy disabled

#### Scenario: Partial text
- **WHEN** the user selects a partly transcribed recording
- **THEN** the latest partial text and any earlier partial attempts can be copied separately, and both retry methods are offered with readiness explanations; an entry without retained audio explains why it cannot be retried

#### Scenario: Retry starts
- **WHEN** an eligible retry is accepted
- **THEN** History yields focus to the previous app and indicates processing with Cancel Retry when viewed again, and duplicate retries are disabled

#### Scenario: Entry changes without a new row
- **WHEN** retry updates the selected entry
- **THEN** History refreshes its status and text even though its ID and the total entry count have not changed

#### Scenario: Daemon or audio unavailable
- **WHEN** Vox is not running or saved audio is missing or unreadable
- **THEN** History explains why retry cannot start and still permits copying existing text and deleting the entry

#### Scenario: Recovery notice
- **WHEN** a failure is saved for retry
- **THEN** the idle tray notice directs the user to History under existing problem priority rules; an audio write failure instead says the recording could not be saved, while an index failure says audio was saved but is not yet available in History

#### Scenario: Recent dictations with failed entries
- **WHEN** the tray builds its recent copy actions
- **THEN** entries with no transcript are omitted and partial transcripts are clearly labelled

### Requirement: Failed Recording Recovery Setting
The General page of Settings on macOS and Linux SHALL offer a "Keep failed recordings for retry" switch, enabled by default, backed by `[transcription] keep_failed_audio`. It SHALL explain that failed audio is saved locally until recovery or deletion, that disabling stops new retention and retries, and that already saved audio can be removed through History. Changes SHALL follow existing immediate-save, comment-preserving, reload, invalid-file, and save-error behavior.

#### Scenario: Settings default
- **WHEN** the user opens General Settings with no explicit recovery setting
- **THEN** the switch is on and its explanation describes local retention

#### Scenario: Turn recovery off
- **WHEN** the user turns the switch off
- **THEN** `keep_failed_audio = false` is saved under `[transcription]` with other settings and comments preserved, and recovery is disabled within the normal reload interval and no later than Settings closing, without a restart

#### Scenario: Turn recovery on
- **WHEN** the user turns the switch back on
- **THEN** `keep_failed_audio = true` is saved and eligible retries and retention are restored within the normal reload interval and no later than Settings closing

#### Scenario: Setting cannot be saved
- **WHEN** the switch change cannot be written
- **THEN** Settings shows "Couldn't Save" and returns the switch to the persisted value

#### Scenario: Invalid configuration
- **WHEN** the configuration cannot be loaded or `keep_failed_audio` has a non-boolean value
- **THEN** existing configuration-error behavior applies and Settings writes nothing through the disabled switch

#### Scenario: Disabled recovery in History
- **WHEN** History displays a previously saved failed recording while recovery is disabled
- **THEN** retry actions are disabled with an explanation pointing to General Settings, while Copy for existing text and Delete remain available
