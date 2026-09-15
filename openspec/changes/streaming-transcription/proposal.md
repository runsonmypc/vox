## Why

Vox currently relies on batch audio transcription (`client.audio.transcriptions.create`), which requires waiting until speech stops before uploading the full audio recording and awaiting model inference. This introduces a 1.0–1.5 second pause before transcribed text appears.

By adopting OpenAI's Realtime streaming API (`gpt-live-transcribe` over WebSockets), audio chunks stream continuously while the user speaks. The server transcribes speech progressively during the turn, reducing post-speech finalization latency to under 200 milliseconds. To prevent cursor jumping, unwanted autocomplete suggestions, and IDE linter triggers caused by live-typing partial text, vox accumulates partial deltas in memory and pastes the completed transcript in a single operation once the user stops speaking.

## What Changes

- **Streaming WebSocket Client**: Implement a dedicated streaming client that connects to OpenAI's Realtime WebSocket endpoint (`wss://api.openai.com/v1/realtime?intent=transcription`) and configures a `gpt-live-transcribe` session with audio input format (24kHz PCM16 mono).
- **Live Chunk Streaming**: Continuously encode and transmit microphone audio chunks during recording using `input_audio_buffer.append` events.
- **Context & Keyword Injection**: Inject user dictionary words, screen OCR context, and active window titles via `session.update` session parameters (`prompt`, `keywords`, `languages`).
- **In-Memory Accumulation**: Buffer incoming `conversation.item.input_audio_transcription.delta` events in memory without emitting live keystrokes into the active application.
- **Single-Shot Paste on Completion**: Upon recording completion (hotkey release or toggle), commit the audio buffer (`input_audio_buffer.commit`), await `conversation.item.input_audio_transcription.completed`, apply snippet expansions, and paste the aggregated text at once via the system clipboard (`Cmd+V`).
- **Resilient Fallback & Mode Configuration**: Allow configuring transcription backend (`mode = "streaming"` or `"batch"`) in `config.toml`, with automatic graceful fallback to the existing batch transcription engine if WebSocket connection or streaming fails.

## Capabilities

### New Capabilities
- `streaming-transcription`: Real-time audio streaming to OpenAI's `gpt-live-transcribe` over WebSockets, in-memory transcript accumulation, and end-of-turn single-shot clipboard injection.

### Modified Capabilities
<!-- None -->

## Impact

- **Dependencies**: Add `websockets` to `pyproject.toml`.
- **Audio Pipeline**: Ensure audio capture provides or resamples to 24kHz PCM16 mono required by OpenAI Realtime audio input format.
- **Configuration**: Add `mode = "streaming"` (default: `"streaming"`, options: `"streaming"`, `"batch"`) and streaming model configuration in `[transcription]` or `[whisper]`.
- **Architecture**: `vox/transcribe.py` gains a streaming transcriber protocol/implementation, coordinate turn lifecycle with `vox/daemon.py` and `vox/audio.py`.
