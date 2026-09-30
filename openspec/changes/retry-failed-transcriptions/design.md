## Context

See proposal.md for motivation. `vox/daemon.py` holds the finished WAV only for `_process`. It saves text through `HistoryDB`, pastes completed text, and falls back from streaming to batch. `PartialTranscriptionError` carries completed parts' text but no resume position. Both batch and whisper.cpp already accept WAV bytes and convert them to their required format.

History is a separate process on both platforms. It accesses SQLite directly and notices inserts through count/max-ID polling; this does not detect an update to an existing entry. There is no daemon control channel today. Existing privacy documentation promises recordings are never saved.

## Goals / Non-Goals

**Goals:** Keep recovery state durable, let the daemon serialize retries with ordinary dictations, and reuse conversion, provider readiness, snippet expansion, and paste behavior. Preserve existing completed History entries during migration.

**Non-Goals:** Replay saved audio through Realtime streaming, resume from a failed chunk, automatically switch providers after a terminal failure, retain successful recordings, provide playback/export controls, or guarantee recovery from a crash before failure persistence completes. No configurable expiry is added in this change.

## Decisions

### 1. Extend History and store original WAVs beside it

Add additive columns for recovery status (`completed`, `failed`, `partial`, `retrying`), original mode, latest attempted mode, audio identifier, safe error summary, and an entry revision. Existing `transcription_mode` continues to describe the provider that produced stored text. Completed legacy rows default to `completed`. Keep distinct partial attempt texts in a child table keyed by recording ID, with attempt time and provider; the main row points to the latest preview. Delete these child rows on full recovery or entry deletion using the existing secure-delete policy. Failed rows explicitly allow empty text while the normal completed-text insertion API continues rejecting blank transcripts.

Save original captured WAV bytes under a generated opaque identifier in an `audio/` directory beside the database: normally `~/.local/share/vox/audio/`. Keeping the original avoids additional lossy conversion and supports either provider through existing converters. External WAV files keep large audio payloads out of SQLite searches and secure-delete transactions; storing blobs in the database would simplify transactions but increase database size and update costs.

Stage each recording as a private directory containing the original WAV and a small versioned manifest with its opaque recording ID, original time, duration, modes, safe failure category, and available partial text. Write files at 0600, flush files and directory, and atomically rename the directory before committing its History association. The manifest permits indexing recovery after a database failure. Constrain all resolved paths to this store and reject symlinks; use a unique recording ID in SQLite to make reconciliation idempotent. Do storage work off the event loop. Sanitize failure summaries: persist a bounded category and user-facing reason, excluding request bodies, dictated text, credentials, and captured context. Preserve timestamp and duration from the recording, plus app type without its title. Store no original screen hints; retries use current prompt and dictionary only.

If audio writing fails, preserve partial text and failure metadata when the database works, report that audio could not be saved, and disable retry. If SQLite fails after the recording directory is committed, report that audio was saved but is not yet available in History. On startup and before opening History, reconcile complete manifests into SQLite once it is writable, preserving the recording ID and original metadata without calling a provider. Skip entries pending deletion. Mark corrupt files unavailable; do not upload them or guess missing metadata. When a manifest ID already exists in SQLite, preserve that row and its current state; do not roll it back to the manifest snapshot. Remove the manifest after indexing succeeds because SQLite then owns its metadata; never reconstruct an indexed recording solely from a leftover WAV. Existing partial-text paste fallback still runs when History is unavailable. Temporary incomplete writes can be cleaned up on startup.

### 2. Retain only terminal transcription errors

Give processing a clear boundary between transcription and downstream delivery. When recovery is enabled, retain full audio on batch/local errors and after both streaming and batch fallback fail. Save a partial failure's text and audio in one logical entry. Do not mistake a paste exception, snippet error, History write error after successful transcription, empty provider result, VAD rejection, or initial cancellation for a transcription failure. Existing ordinary paste behavior stays in place.

This follows the requested failure recovery scope. Persisting every recording before transcription would also protect against crashes but would retain successful audio temporarily and broaden the storage behavior.

### 3. Let the daemon own retries through a small local control channel

Use an asyncio Unix-domain socket in Vox's owner-only runtime directory with socket mode 0600. Pass its location when launching History. Support bounded messages for status/readiness, retry, cancel, delete, and clear; requests contain entry IDs and an explicit `batch` or `whisper_cpp` mode, never paths or executable names. Validate all requests and resolve audio identifiers inside the managed store. Start the service with the daemon, stop it on shutdown, and remove only its own stale endpoint under the existing single-instance lifecycle.

The daemon checks recovery is enabled, idle state, Settings state, the last successfully loaded configuration, selected provider readiness, and the requested entry revision before accepting. A valid local retry is independent of missing cloud credentials or a broken default cloud provider. Snapshot current settings for the attempt and build the explicitly selected transcriber without mutating the global mode. Use one processing task and a generation token tied to the entry; stale callbacks cannot end a newer task.

Running retries in the History subprocess was considered, but would duplicate provider/key handling and allow transcription outside the daemon's busy/cancel rules. A database job queue would avoid a socket but introduce polling and stale job ownership. The socket provides immediate admission and serialized deletion as well as status.

Bind each control connection to the same database/store identity as its History window so `--db` cannot target unrelated daemon entries. An unreachable socket does not prove the daemon is absent: use the existing daemon ownership lock to distinguish absence from connection failure, and report the latter instead of falling back to unsynchronized writes. While the daemon is running, route History delete/clear through it so cancellation and finalization share the daemon's execution order. When the daemon is absent, History can still copy text and use the shared storage deletion API directly; retry explains that Vox must be running. Offline deletion and startup use conditional database updates so a removed entry is never reinserted by a stale task.

### 4. Retry the full recording and deliver through the existing paste path

Accepted retry sets `retrying`, keeps audio and prior text, and moves the daemon to PROCESSING. History then hides or minimizes and yields focus to the previous app before sending an explicit ready-to-run acknowledgement. The daemon waits for this acknowledgement with a bounded timeout before starting work; this avoids pasting a fast local result into History. Closing History after the acknowledgement does not cancel the job. If another external app is selected, paste follows it. If History or another Vox window has focus at completion, retain the result for copying and report delivery as skipped. Capture the external app type after focus release for the Linux shortcut fallback; never reuse a window ID from the original recording. Detecting the paste target does not collect transcription hints. An acknowledgement timeout or disconnect before acknowledgement restores the saved failed/partial state without a provider call.

Use no live session, fresh screen capture, or window-title hints. Bypass the recording-start VAD gate for an explicit retry so a saved recording is actually attempted. Reuse batch splitting and SDK retry rules. Retrying a partial recording retranscribes the entire audio, which may charge again for completed parts; show this in retry help. Resuming individual parts would require storing provider-specific chunk boundaries and is outside this change.

On a full non-empty result, apply current snippets once, conditionally update the existing row to completed with the actual provider, and paste using existing platform and clipboard rules. Preserve original timestamp/duration; replace partial text rather than append. Remove audio only after the transcript commit. A paste failure leaves completed text available and plays the error sound. If the text commit fails, attempt paste only if focus and cancellation permit, report the save error, and retain the audio and prior entry. A conditional update rejected because the entry was deleted or superseded is a stale result, not a storage failure, and must never trigger this paste fallback.

On a failed or empty retry, or cancellation before full transcript commit, retain audio and prior text and restore `partial` or `failed`. Record the latest safe outcome and attempted mode. Save each distinct non-empty partial result as an attempt under the same recording and make the latest result the preview, retaining earlier attempts for separate copying. Keep the provider attached to each text rather than attributing older text to the latest failed attempt. Do not concatenate partial attempts. Remove prior partial attempts when a full transcript is committed. If a partial attempt cannot be saved, report the failure and use the existing partial-text paste fallback after checking cancellation and focus eligibility. Cancellation before full transcript commit leaves saved recovery data intact; after commit it keeps the completed row and allows cleanup but suppresses paste that has not begun. Once the keystroke has been sent, cancellation still completes clipboard restoration. Startup resets stale `retrying` states and never automatically resubmits audio or replays paste. Explicit retries remain allowed when hotkeys are paused; History offers Cancel Retry independently of the paused hotkeys. No additional Vox retry loop is added around the SDK.

### 5. Track audio cleanup durably

When recovery commits or an entry is deleted, record its recording-directory identifier in a pending-cleanup table in the same transaction that clears the association or deletes the row. Then remove the managed WAV, any manifest, and the recording directory, and clear the cleanup marker. Delete/clear first cancels any associated retry and awaits termination; generation and row-revision checks reject late results before update or paste. Clear History includes failures with empty text, child partial attempts, all audio associations, pending cleanup, and unindexed recording directories. Reconciliation and Clear run under the same store lock, including during daemon startup and offline deletion. Commit cleanup markers with deletions and check them before importing manifests; when SQLite is unavailable, report that Clear could not complete rather than falsely claiming deletion. A later reconciliation must check cleanup markers before import.

An unlink failure is reported and retried on startup using only managed identifiers. Completed transcripts stay readable while cleanup is pending. Do not claim secure erasure of WAV files on SSDs; existing SQLite secure-delete behavior remains for transcript text. Keeping audio until recovery/deletion avoids silently discarding old failed words, with documented disk growth as the trade-off.

### 6. Extend both History UIs and their refresh mechanism

Add failure labels, details, separately copyable partial attempts, Local and OpenAI Batch actions, Cancel Retry, cloud-upload wording, readiness explanations, progress, and current errors to the shared History model and both native windows. Empty failures have a meaningful preview and disabled Copy; partial entries keep Copy. The recent tray menu includes only entries containing text and labels partial text.

Replace count/max-ID-only refresh detection with a persisted History change revision advanced by inserts, updates, deletes, and clear, so live windows notice retry completion without new rows. Reuse existing search ordering and literal Unicode text matching. Add a recovery notice below configuration/key/mode problems in tray priority, and report retention failures distinctly.

### 7. Add a default-on Settings switch

Add `keep_failed_audio: bool = True` to configuration, persisted as `[transcription] keep_failed_audio`, using existing type validation, mutation, and hot-reload paths. A missing key in older configurations enables recovery without rewriting the file. Both General Settings implementations expose "Keep failed recordings for retry" with a short explanation of local retention and how to delete old recordings.

Read the effective setting at the terminal failure boundary before writing any recovery audio. Serialize recording-directory publication with configuration updates; when disabling wins before publication, invalidate the write and remove its staged files. Await outstanding storage workers before cleanup so a cancelled worker cannot publish late. When off, use prior failure handling: preserve partial text in ordinary History or paste it when History cannot save it, but create no empty failure entry and write no recovery WAV. This gate also applies to streaming's terminal batch fallback failure. Turning off is an opt-out from retention and retries, while explicit Delete/Clear remains the way to remove already retained audio.

Check the setting independently at retry admission; advertise the disabled reason to History. Re-enabling restores eligibility for retained recordings. Settings cannot open during processing, but manual file edits can hot reload during work: disabling lets an initial transcription finish while preventing failure retention, and cancels an active retry according to its full-transcript commit and paste boundaries. Do not snapshot this privacy choice with provider settings or defer it until the next recording. Copy/delete, metadata reconciliation, and pending cleanup remain available while off. Failed configuration reloads keep the last valid settings, including this switch, as existing reload semantics require; an invalid startup configuration blocks retries until a valid configuration loads. A switch saved in Settings applies within the reload interval and no later than closing Settings.

## Risks / Trade-offs

- [Audio consumes disk until recovery or deletion] → Document its location and lifecycle, preserve recording limits, and make delete/clear remove associated files.
- [Database and WAV writes are not one transaction] → Atomic file writes, durable cleanup markers, recoverable manifests, and idempotent startup reconciliation avoid silent loss or false retry claims.
- [Fast completion while History holds focus] → Require focus release acknowledgement before transcription begins; test with instant fake providers on both platforms.
- [Provider billing on full retries] → Keep retries explicit and explain that batch sends the full recording again, including previously transcribed parts.
- [Window process and daemon race with deletion] → Serialize online deletion through the daemon, cancel first, and condition completion on entry revision and task generation.
- [Stored error text leaks input or secrets] → Persist curated safe summaries, and keep audio/transcript bodies out of INFO logs and control messages.

## Migration Plan

1. Add an idempotent transactional SQLite migration preserving all existing rows, IDs, text, timestamps, and metadata. Create private recording storage and initialize History revision tracking.
2. Ship storage and daemon support with the control channel, then wire shared and platform History controls into it. Failures saved headlessly become available when History is opened later.
3. Add default-on configuration and the General Settings switch on both platforms; update privacy, usage, settings, and troubleshooting docs and README claims about audio retention and explicit cloud retry.
4. Validate migration against an old database and exercise the acceptance scenarios with injected provider, storage, and paste failures on both platforms.

Rollback can run an older app against the additive schema for completed transcripts, but it does not understand recovery entries or clean retained audio. Keep a database backup before migration and document retained audio cleanup; avoid downgrading with pending recovery entries until they are recovered or deleted. An older version can display empty failure rows incorrectly and Clear History in that version cannot remove retained audio.
