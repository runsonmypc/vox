## 1. Dependencies and Configuration

- [x] 1.1 Add `websockets` dependency to `pyproject.toml` and verify successful import and installation in the virtual environment via `uv pip` / `pip list`
- [x] 1.2 Update `vox/config.py` to support transcription `mode` (`"streaming"` or `"batch"`, defaulting to `"streaming"`) and streaming model configuration, verifying parsing via unit test

## 2. Audio Capture and Streaming Pipeline

- [x] 2.1 Update `vox/audio.py` to provide 24kHz PCM16 audio chunks suitable for OpenAI Realtime input while maintaining 16-bit little-endian format, verifying output chunk rates and byte format
- [x] 2.2 Add an asynchronous chunk generator / queue to `Recorder` enabling live streaming consumption during recording while preserving the complete turn audio buffer in memory, verified by a unit test

## 3. Streaming Transcription Engine

- [ ] 3.1 Implement `StreamingTranscriber` in `vox/streaming.py` that opens a WebSocket connection to `wss://api.openai.com/v1/realtime?intent=transcription` with API key headers and issues `session.update` with prompt, keywords, and `gpt-live-transcribe`, verifying session setup with unit tests
- [ ] 3.2 Implement chunk streaming via `input_audio_buffer.append` and in-memory delta accumulation (`conversation.item.input_audio_transcription.delta`), verifying that deltas aggregate in memory without emitting external keystrokes
- [ ] 3.3 Implement turn finalization: send `input_audio_buffer.commit`, await `conversation.item.input_audio_transcription.completed`, and return the complete accumulated transcript, verified by mock test

## 4. Daemon Integration and Fallback

- [ ] 4.1 Integrate `StreamingTranscriber` into `vox/daemon.py` to stream chunks in real time while recording, commit on key release/stop, expand snippets, and trigger single-shot paste via `injector.paste(text)`
- [ ] 4.2 Implement graceful fallback in `vox/daemon.py` to batch `WhisperTranscriber` if WebSocket connection or streaming encounters an error, verified with a connection failure simulation test
- [ ] 4.3 Perform end-to-end verification of streaming dictation: verify sub-200ms paste latency after stop, single-shot clipboard injection, and accurate custom vocabulary recognition
