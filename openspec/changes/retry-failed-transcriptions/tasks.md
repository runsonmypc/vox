## 1. Durable recovery storage

- [x] 1.1 Add boolean `[transcription] keep_failed_audio`, default true, through loading, validation, mutation, and hot reload; verify absent-key defaults, explicit false across restarts, invalid reloads preserving the last valid setting, non-boolean rejection, and comment preservation.

- [x] 1.2 Add an idempotent additive History migration, recovery fields, conditional entry updates, and persisted change revision; verify migration preserves legacy rows and update/delete operations refresh an already-open History model.
- [x] 1.3 Implement private original-WAV storage with atomic writes, safe identifiers, and failure metadata; verify byte-for-byte audio round trips, 0600/0700 permissions, normal restart persistence, disk-write failures, database failures after audio is saved, manifest reconciliation without duplicate entries, and rejection of corrupt audio or symlink paths.
- [x] 1.4 Implement shared delete/clear and durable pending audio cleanup; verify associated files are removed, unlink failures are reported and retried after restart, Clear includes unindexed recordings and partial attempts, and reconciliation cannot resurrect deleted recordings.

## 2. Capture transcription failures

- [x] 2.1 Separate terminal transcription errors from delivery errors and save complete audio for local, batch, first-part, and partial failures only when recovery is enabled; verify each produces one failed entry with available partial text and audio, unavailable History retains the existing partial-text paste fallback, and disabled recovery writes no WAV or empty failure entry while preserving partial text.
- [x] 2.2 Integrate streaming failure retention after batch fallback also fails; verify disabled recovery retains no audio after fallback failure, and successful fallback, initial cancellation, VAD rejection, empty results, and paste failures never create recovery audio.
- [x] 2.3 Add safe retention/error notices and adjust recent copy entries; verify tray problem priority remains correct, empty failures are omitted from copy actions, partial entries are labelled, and normal logs contain no dictated text or credentials.

## 3. Daemon control and retry execution

- [x] 3.1 Add the owner-only Unix socket lifecycle and bounded validated request protocol for status, retry, focus-release acknowledgement, cancel, delete, and clear; verify malformed requests, invalid modes/IDs, stale revisions, unsafe identifiers, wrong database/store identities, and missing daemon connections fail with actionable errors; verify offline deletion requires confirmed absence of the daemon.
- [x] 3.2 Implement selected-provider readiness and snapshot current retry settings without changing the default mode; verify both same-method and cross-method retries, local retry with a missing cloud key, no retry screen/window hints, no streaming replay, and full-recording input through existing converters.
- [x] 3.3 Admit retry through the daemon's processing state and focus-release acknowledgement; verify recovery-disabled/busy/Settings/configuration gates, duplicate requests, missing acknowledgements, hotkeys during processing, and instant provider completion cannot paste into History before it yields focus.
- [x] 3.4 Implement successful recovery update, snippet expansion, and automatic paste with clipboard restoration; verify one original History row holds the full text and actual provider, original timestamp/duration are retained, old partial text is replaced, paste failure preserves completed text, and History-save failure preserves audio while attempting an eligible paste, and stale/deleted-row results never use that fallback.
- [x] 3.5 Handle repeat errors, new partial results, empty results, cancellation, and shutdown; verify audio and prior text survive, distinct partial results remain separately copyable under one recording with accurate providers, stale processing events are ignored, interrupted retry states recover on restart, and disabling through valid hot reload obeys transcript-commit/paste boundaries, invalid reloads retain the last valid setting, and staged audio cannot publish after retention is disabled.
- [x] 3.6 Serialize online delete/clear with retry cancellation and conditional finalization, retaining offline deletion support; verify deletion during provider completion prevents late paste and row recreation, paste already begun finishes clipboard restoration, and pending audio cleanup remains durable.

## 4. History controls on both platforms

- [x] 4.1 Add "Keep failed recordings for retry" to General Settings on macOS and Linux with local-retention and existing-audio deletion help; verify default on, immediate persisted changes, save-error rollback, invalid-file disabling, and application within the reload interval and no later than window close without restart.

- [x] 4.2 Extend the shared History model with failed previews, method choices, safe details, readiness, progress, errors, and daemon control integration; verify empty and partial entries, literal Unicode search, failed Copy disabling, updates to existing rows, disabled retry explanations while recovery is off, restored eligibility after re-enabling, and Cancel Retry while hotkeys are paused.
- [x] 4.3 Add native macOS retry controls and focus release; verify Local and OpenAI Batch retries with upload wording, unavailable states, progress on reopening, copying partial text, Cancel Retry, separately copyable partial attempts, no paste into a reopened History window, and delete/clear cancellation in the AppKit window.
- [x] 4.4 Add equivalent Linux retry controls and focus release; verify the same recovery actions, separate partial-attempt copying, Cancel Retry, focus safeguards, errors, progress, and deletion behavior in the GTK window, including narrow layouts.

## 5. Documentation and integration validation

- [x] 5.1 Update README, privacy, usage, settings, and troubleshooting documentation; verify all claims that recordings are never saved are corrected and the docs explain storage location, retention/deletion, explicit batch upload and possible repeat charges, local recovery, automatic paste, the default-on switch, and the effect of disabling on new and existing recordings.
- [x] 5.2 Add end-to-end recovery coverage with fake providers and a temporary database/audio store; verify failed capture followed by restart and cross-method retry, partial recovery without duplication, clipboard/paste outcomes, storage faults and reconciliation, cancellation before/after transcript commit and paste, no automatic retry/paste after restart, and deletion races against the specification scenarios.
- [x] 5.3 Run the repository's required lint/test checks and manually exercise focus release and automatic paste on macOS and Linux; record results and any environment limitations before marking the change ready for archive.
