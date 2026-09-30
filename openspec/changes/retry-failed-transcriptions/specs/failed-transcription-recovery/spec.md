## Purpose

Preserves audio from failed dictations locally so users can recover the same recording with local or OpenAI batch transcription after a failure or restart.

## ADDED Requirements

### Requirement: Persistent Private Recovery Audio
When failed-transcription recovery is enabled, the system SHALL retain the full captured WAV and its failure metadata across normal restarts. Recovery audio SHALL be readable only by its owner, with files at mode 0600 and Vox-created directories at mode 0700. It SHALL store no API key, raw window title, screenshot, or screen text as recovery metadata. Persistence failures SHALL be reported accurately without claiming that unsaved audio is retryable, and partial text SHALL still be preserved when possible.

#### Scenario: Restart after failure
- **WHEN** Vox restarts after a failed recording was saved and recovery is enabled
- **THEN** History offers retries using the same complete audio and original recording timestamp and duration

#### Scenario: Cannot save audio
- **WHEN** audio storage fails, such as because disk space is exhausted
- **THEN** Vox reports that the recording could not be saved, offers no retry for unavailable audio, and preserves any partial transcript when possible

### Requirement: Explicit Retry Method Selection
When failed-transcription recovery is enabled, the system SHALL allow a failed recording to be retried with Local (whisper.cpp) or OpenAI Batch regardless of its original method, using the complete saved audio. It SHALL check the selected method's readiness independently of the default mode, use a snapshot of current language, prompt, dictionary, model, and snippet settings, and leave the default mode unchanged. It SHALL NOT capture the screen or use window-title hints for a retry. OpenAI Batch retries SHALL require an explicit user choice and SHALL be labelled as sending saved audio to OpenAI. Saved audio SHALL NOT be replayed through streaming.

#### Scenario: Local failure retried with batch
- **WHEN** the user explicitly chooses OpenAI Batch for a failed local recording and its setup is ready
- **THEN** the saved full recording is submitted through batch transcription using current batch settings

#### Scenario: Cloud failure retried locally
- **WHEN** the user chooses Local for a failed batch or streaming recording and whisper.cpp is ready
- **THEN** the saved full recording is transcribed locally without a network request, even if the default cloud mode has no key

#### Scenario: Same method after setup is fixed
- **WHEN** the user fixes the original method's setup and retries with that method
- **THEN** the same audio is transcribed without re-recording or changing the configured default mode

#### Scenario: Method unavailable
- **WHEN** the selected method lacks its API key, executable, or model, or Vox has no successfully loaded configuration
- **THEN** retry is refused with an explanation and the recording and prior text are retained

### Requirement: Retry Delivery and Failure Handling
A successful retry producing a non-empty full transcript SHALL replace the failed entry's text with the complete result after snippet expansion, record the actual successful provider, and automatically paste into the focused external app subject to the focus and cancellation boundaries below, using existing clipboard restoration rules. The system SHALL retain the original recording timestamp and duration. It SHALL NOT append the full transcript to old partial text or create a duplicate History entry. Another failed or empty retry, or cancellation before the full transcript commit, SHALL keep saved audio and prior text available. Each distinct non-empty partial result SHALL remain copyable under the same recording with its attempt time and provider until full recovery or deletion. The latest partial result SHALL be the main preview; earlier partial results SHALL remain separately accessible and SHALL NOT be concatenated with it. A full recovered result SHALL replace these partial results.

#### Scenario: Recovery succeeds
- **WHEN** retry produces a complete non-empty transcript
- **THEN** the original entry is updated to completed, the transcript is automatically pasted when focus and cancellation permit, and no duplicate entry or duplicated partial words appear

#### Scenario: Paste fails after recovery
- **WHEN** retry transcription succeeds but automatic paste fails
- **THEN** the complete transcript stays in History for copying, the paste error is reported, and no further transcription starts

#### Scenario: Another attempt fails or returns empty
- **WHEN** retry fails, fails partway, or returns empty text
- **THEN** the recording remains retryable and prior text remains available, with the latest outcome shown

#### Scenario: Recovered text cannot be saved
- **WHEN** retry produces a full transcript but updating History fails
- **THEN** Vox attempts the requested paste, reports the save failure, and retains audio and the prior entry for another recovery attempt

#### Scenario: A later retry produces different partial text
- **WHEN** a retry produces non-empty partial text that differs from an earlier attempt
- **THEN** both results remain separately copyable within the same recording, labelled with their attempt time and provider, and the latest result becomes the preview without joining their words

#### Scenario: Partial retry text cannot be saved
- **WHEN** a new partial result cannot be saved
- **THEN** Vox reports the save failure, retains the audio and previously saved text, and attempts the existing partial-text paste fallback only if focus and cancellation permit

### Requirement: Serialized Retry Execution
The system SHALL admit retries only while recovery is enabled, the daemon is idle, Settings is closed, and a valid configuration has been loaded. Invalid edits after a successful load SHALL retain the last valid settings, following existing reload behavior. It SHALL run at most one dictation or retry at a time, remain responsive during transcription, and refuse duplicate or stale requests. Cancelling a retry or shutting down before a full transcript commit SHALL preserve the previously saved recording. History SHALL offer Cancel Retry while an attempt is active, including while hotkeys are paused. Deleting an entry or clearing History SHALL cancel any associated retry and prevent a result not yet delivered from pasting or recreating deleted data. Cancellation before a full transcript commit SHALL retain audio and prior text; cancellation after that commit SHALL preserve the completed transcript and suppress only paste that has not begun. A paste keystroke already sent cannot be undone; clipboard restoration SHALL still finish. A retry SHALL NOT automatically resume or paste after a daemon restart.

#### Scenario: Busy daemon
- **WHEN** retry is requested during recording, processing, or Settings editing
- **THEN** Vox refuses the request with a reason and retains the entry unchanged

#### Scenario: Cancel or restart during retry
- **WHEN** retry is cancelled or Vox stops before a full transcript is committed
- **THEN** the saved recording and prior text remain available for a later attempt and no late transcript is pasted

#### Scenario: Deletion during retry
- **WHEN** the entry is deleted or History is cleared during its retry
- **THEN** the attempt is cancelled, any paste not already begun is suppressed, its late result is ignored, and its audio and entry are deleted; clipboard restoration still finishes if paste already began

### Requirement: Recovery Audio Lifecycle
The system SHALL keep failed audio until a full non-empty recovered transcript is durably saved or the user deletes its entry or clears History. Successful recovery SHALL remove retained audio even if paste subsequently fails. Deletion SHALL remove associated audio as well as text; cleanup errors SHALL be surfaced and pending cleanup retried on the next startup. The system SHALL NOT silently expire failed recordings. Clear History SHALL cover all managed retained recordings, including audio not yet indexed in History and pending cleanup, and SHALL report any files it could not remove.

#### Scenario: Complete recovery
- **WHEN** a full recovered transcript is durably saved
- **THEN** the entry remains as completed text and its recovery audio is removed

#### Scenario: Delete or clear
- **WHEN** a failed entry is deleted or the user confirms Clear History
- **THEN** associated audio is removed along with the entries, or any cleanup failure is reported and retried on startup

#### Scenario: Unresolved recording
- **WHEN** time passes without recovery or deletion
- **THEN** the failed recording remains available without automatic expiry

### Requirement: Recovery Can Be Disabled
Failed-transcription recovery SHALL be enabled by default and controlled by the boolean `[transcription] keep_failed_audio` setting. Turning it off SHALL prevent new failed-audio retention and refuse all retry requests, including requests for previously saved recordings. It SHALL preserve existing saved audio and text for copying or explicit deletion; re-enabling SHALL restore eligible retries. Ordinary transcript history and partial-text fallback SHALL continue while recovery is off. The system SHALL use the setting effective when handling a terminal transcription failure, and SHALL cancel an active retry if a valid hot reload disables recovery, using the cancellation rules above. Disabling SHALL invalidate an unfinished initial retention write, remove its uncommitted audio, and preserve available partial text.

#### Scenario: Default and persisted choice
- **WHEN** the setting is absent, including in an existing configuration
- **THEN** recovery is enabled; an explicitly saved false value remains off after restart

#### Scenario: New failure with recovery off
- **WHEN** transcription fails while `keep_failed_audio = false`
- **THEN** no recovery WAV or empty failed-recording entry is saved, and any partial text is saved to ordinary History or pasted through the existing fallback

#### Scenario: Previously retained recording with recovery off
- **WHEN** the user turns recovery off with failed recordings already saved
- **THEN** their text remains copyable and their entries and audio remain deletable, retry actions explain that recovery is disabled, and direct retry requests are rejected

#### Scenario: Re-enable recovery
- **WHEN** the user turns recovery back on
- **THEN** eligible retained recordings can be retried and future transcription failures retain audio again

#### Scenario: Disable while work is running
- **WHEN** a hot reload turns recovery off during an initial transcription or a retry
- **THEN** an initial transcription continues but any later failure saves no recovery audio; an active retry is cancelled using the transcript-commit and paste boundaries above, and any initial retention write not yet committed is discarded

#### Scenario: Invalid edit after disabling
- **WHEN** recovery was successfully disabled and a later configuration edit fails validation
- **THEN** recovery remains disabled under the last valid configuration

#### Scenario: Disable while audio is being saved
- **WHEN** a valid configuration reload disables recovery before a new recording is durably published in the recovery store
- **THEN** that pending write is invalidated and its temporary files are removed; any already published recording remains subject to explicit deletion

### Requirement: Recovery Storage Reconciliation
If a complete recording was saved but its History index update failed, Vox SHALL report that the audio is saved but not yet available in History. Once storage is writable, startup or reopening History SHALL reconcile that recording into one entry without retranscription. Missing or corrupt audio SHALL produce an unavailable state without sending data to a provider. Clear History SHALL prevent deleted recordings from reappearing during reconciliation.

#### Scenario: Audio saved but History write fails
- **WHEN** audio is durably saved and the database write fails
- **THEN** Vox reports the index failure and keeps the audio; after the database becomes writable and History is reopened or Vox restarts, one recovery entry appears without a provider request

#### Scenario: Missing or corrupt audio
- **WHEN** a retry refers to missing, truncated, or invalid saved WAV data
- **THEN** the retry is rejected before calling a provider, an actionable error is shown, and existing text remains copyable and deletable

#### Scenario: Clear includes unindexed audio
- **WHEN** the user confirms Clear History while some retained recordings lack database entries
- **THEN** those recordings are removed too, or a cleanup error is reported, and reconciliation does not recreate deleted entries

#### Scenario: History migration preserves existing dictations
- **WHEN** an existing History database is first opened by the updated version
- **THEN** existing transcript text, IDs, timestamps, durations, and providers remain intact and readable without requiring audio files

### Requirement: Retry Focus and Delivery Boundaries
Vox SHALL wait for History to yield focus before starting a retry. If focus cannot be released, the retry SHALL return to an available failed state with an explanation and no provider request. At completion, Vox SHALL automatically paste once into the currently focused external app. If a Vox window has focus or no external target can be established, Vox SHALL retain the completed transcript in History and report that it is ready to copy without sending a paste keystroke. An interrupted delivery SHALL never be automatically repeated after restart.

#### Scenario: User switches apps during retry
- **WHEN** History yields focus and the user selects another external app before the retry finishes
- **THEN** the recovered transcript is pasted into that app using its current paste shortcut

#### Scenario: History regains focus
- **WHEN** the user reopens History and it has focus when retry finishes
- **THEN** the completed transcript is saved and shown for copying, and nothing is pasted into History

#### Scenario: Focus release fails
- **WHEN** History cannot yield focus or closes before acknowledging release
- **THEN** no provider request starts, the daemon returns to idle, and the saved recording remains eligible for retry

#### Scenario: Cancel after transcript commit
- **WHEN** retry is cancelled after its full transcript was saved but before paste began
- **THEN** the completed transcript stays saved, audio cleanup proceeds, and no paste starts

#### Scenario: Cancel after paste started
- **WHEN** cancellation or deletion arrives after a paste keystroke was sent
- **THEN** Vox does not attempt to undo the pasted text, finishes clipboard restoration, and sends no further paste keystrokes
