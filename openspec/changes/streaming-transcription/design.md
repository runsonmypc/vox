## Context

Currently, vox operates in batch mode: `vox/audio.py` buffers audio until recording stops, then `vox/transcribe.py` uploads the entire WAV file to OpenAI's batch transcription endpoint (`client.audio.transcriptions.create`), causing a 1.0–1.5s post-recording delay.

OpenAI's Realtime API provides `gpt-live-transcribe` over WebSockets (`wss://api.openai.com/v1/realtime?intent=transcription`), which accepts streaming 24kHz PCM16 audio chunks and emits live transcription deltas. By streaming audio during speech and accumulating deltas in memory, vox can achieve sub-200ms paste latency upon speech termination while eliminating the UX pitfalls of live character typing.

See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**
- Stream audio chunks to OpenAI Realtime WebSocket in real time during active recording.
- Accumulate incoming transcription deltas in memory without modifying the user's cursor or document while speaking.
- Finalize transcription via `input_audio_buffer.commit` upon key release/stop, expand snippets, and paste the entire transcript in a single clipboard operation.
- Provide automatic fallback to batch transcription if WebSocket connection or streaming encounters an error, ensuring speech is never lost.
- Support configuration of transcription mode (`streaming` vs `batch`) in `~/.config/vox/config.toml`.

**Non-Goals:**
- Live character-by-character typing into target applications (causes autocomplete, linter, and cursor jumps in developer workflows).
- Server-side VAD turn truncation (vox retains push-to-talk / key-driven turn control with `turn_detection: null`).
- Multi-turn conversational voice agent functionality (vox remains focused on speech-to-text dictation).

## Decisions

### 1. Networking: Async WebSockets via `websockets`
- **Choice**: Use the standard `websockets` async client library.
- **Rationale**: Minimal footprint, native `asyncio` integration, full control over connection lifecycles, and resilient header / error handling.
- **Alternative Considered**: Official OpenAI Realtime beta SDK client. Rejected because the beta Realtime client adds significant dependency weight and frequently changes internal event abstractions, whereas direct WebSocket event handling is concise and transparent.

### 2. Audio Sampling: 48kHz Capture with 2x Decimation to 24kHz
- **Choice**: Record audio at 48kHz PCM16, decimate by 2 (`samples[::2]`) to produce 24kHz PCM16 for OpenAI Realtime, and pass 48kHz frames to WebRTC VAD (which natively supports 48kHz).
- **Rationale**: OpenAI Realtime strictly mandates 24kHz PCM16 audio. Recording at 48kHz satisfies WebRTC VAD natively and yields 24kHz with simple, lossless, dependency-free integer decimation (`samples[::2]`), avoiding floating-point resampling filters.
- **Alternative Considered**: 16kHz capture with 2:3 rational interpolation. Rejected due to added resampling complexity and potential audio artifacts.

### 3. Session Lifecycle and Context Injection
- **Choice**: On recording start, establish the WebSocket connection with `Authorization: Bearer <API_KEY>` and `OpenAI-Beta: realtime=v1`. Immediately transmit `session.update`:
  ```json
  {
    "type": "session.update",
    "session": {
      "type": "transcription",
      "audio": {
        "input": {
          "format": { "type": "audio/pcm", "rate": 24000 },
          "transcription": {
            "model": "gpt-live-transcribe",
            "prompt": "<active app and screen OCR context>",
            "keywords": ["<custom dictionary terms>"],
            "languages": ["<language>"]
          },
          "turn_detection": null
        }
      }
    }
  }
  ```
- **Rationale**: Disabling server VAD (`turn_detection: null`) preserves user control over speech boundaries via the physical hotkey. Sending keywords and prompt at session initialization steers acoustic recognition toward project-specific terms.

### 4. In-Memory Delta Accumulation and Atomic Paste
- **Choice**: Collect incoming `conversation.item.input_audio_transcription.delta` tokens in an in-memory buffer. When the hotkey is released or recording stops:
  1. Send `input_audio_buffer.commit`.
  2. Await `conversation.item.input_audio_transcription.completed`.
  3. Validate against empty/hallucinated text.
  4. Perform snippet substitution.
  5. Paste the final string via `injector.paste(text)` in a single `Cmd+V` keystroke.
- **Rationale**: Delivers instant response upon key release while protecting terminals and code editors from erratic partial edits, backspaces, or auto-closing delimiters.

### 5. Resilient Audio Caching and Batch Fallback
- **Choice**: Maintain raw recorded audio in memory concurrently during streaming. If WebSocket connection, authorization, or chunk transmission raises any exception:
  1. Log a warning with the streaming error.
  2. Revert immediately to batch transcription using the preserved audio buffer.
  3. Seamlessly paste the batch result.
- **Rationale**: Prevents lost dictations due to temporary network fluctuations or API rate limits.

## Risks / Trade-offs

- **[Risk] WebSocket connection latency on hotkey press** → **Mitigation**: Connect WebSocket concurrently as the first audio frame arrives or maintain an optional warm connection pool if latency warrants it. In practice, WebSocket handshake takes <100ms, which overlaps with the user taking a breath before speaking.
- **[Risk] Network drops or API errors mid-speech** → **Mitigation**: Audio frames are held in memory locally throughout the session. If the WebSocket connection fails at any point, vox transparently falls back to batch transcription.
- **[Risk] High API cost with streaming** → **Mitigation**: Allow users to toggle `mode = "batch"` or `mode = "streaming"` in `config.toml`, defaulting to user preference.
- **[Risk] Empty/silent turns incurring commit overhead** → **Mitigation**: WebRTC VAD and RMS energy thresholds abort empty turns locally before issuing commits.
