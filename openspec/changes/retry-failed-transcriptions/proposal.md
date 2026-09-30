## Why

Failed dictations currently lose their audio once processing ends; only text from completed parts survives a partial batch failure. Keeping failed recordings lets users recover their words after fixing a provider problem or by choosing a different transcription method.

## What Changes

- Add a General Settings switch, "Keep failed recordings for retry", enabled by default. Turning it off stops new failed-audio retention and disables retries; existing recordings stay available for deletion and can be retried after re-enabling.
- When recovery is enabled, save the complete WAV locally when local or batch transcription fails, including when streaming and its batch fallback both fail.
- Show failed and partially transcribed recordings in History, including failures with no transcript, and keep them available after restarting Vox.
- Offer Retry with Local and Retry with OpenAI Batch, using the same saved audio without changing the default transcription mode. A streaming failure is recovered through either of these methods.
- Save successful recovery to History and automatically paste into the focused external app. History yields focus when retry starts; if a Vox window has focus at completion, the text is ready to copy from History.
- Retain audio and each partial attempt after another failure or cancellation before completion. Remove audio after a complete transcript is saved, or when the entry is deleted or history cleared.
- Explain recovery storage failures, missing audio, unavailable methods, and retry progress; reconcile saved audio into History after an index failure and include it in Clear History. Update privacy and usage documentation for retained recordings and explicit cloud retries.

## Capabilities

### New Capabilities

- `failed-transcription-recovery`: Private, persistent failed audio storage, retry method selection, recovery delivery, and recording lifecycle.

### Modified Capabilities

- `dictation-pipeline`: Preserve full audio on terminal transcription errors and partial failures while retaining existing partial-text fallback behavior.
- `desktop-frontend`: Add the default-on recovery Settings switch, include failed recordings in History and provide retry controls, progress, errors, and deletion of retained audio.

## Impact

- Python daemon processing and state handling, SQLite history schema and migration, and a local recording store beside the history database.
- Configuration loading and hot reload, General Settings on both platforms, shared History model, native AppKit and GTK History windows, tray notices, and an owner-only local control channel from History to the daemon.
- Reuse existing OpenAI batch and whisper.cpp transcribers, readiness checks, audio conversion, snippet expansion, and clipboard paste behavior; no new transcription provider.
- Privacy, usage, troubleshooting, and README documentation. Retained audio increases local disk usage until recovery or deletion; an explicit OpenAI batch retry uploads the saved recording and can incur another charge.
