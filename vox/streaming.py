"""OpenAI Realtime streaming transcription client over WebSockets."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
from typing import Any

import websockets

from .config import Config
from .errors import StreamingError
from .transcribe import _extract_vocab, is_prompt_hallucination
from .window import AppContext

log = logging.getLogger(__name__)

REALTIME_WS_URL = "wss://api.openai.com/v1/realtime?intent=transcription"


def build_session_update(config: Config, context: AppContext | None = None) -> dict[str, Any]:
    """Build the session.update payload for gpt-live-transcribe with prompt & keywords."""
    keywords: list[str] = list(config.dictionary)
    prompt_parts: list[str] = []

    if config.whisper_prompt:
        prompt_parts.append(config.whisper_prompt)

    if context:
        if context.window_title:
            prompt_parts.append(f"Window: {context.window_title}")
            keywords.extend(_extract_vocab(context.window_title, max_words=10))
        if context.screen_text:
            prompt_parts.append(f"Screen: {context.screen_text[:500]}")
            keywords.extend(_extract_vocab(context.screen_text, max_words=25))

    # Deduplicate keywords preserving order
    seen: set[str] = set()
    unique_keywords: list[str] = []
    for k in keywords:
        lower = k.lower()
        if lower not in seen:
            seen.add(lower)
            unique_keywords.append(k)

    prompt = "\n".join(prompt_parts)

    transcription_cfg: dict[str, Any] = {
        "model": config.streaming_model or "gpt-live-transcribe",
    }
    if prompt:
        transcription_cfg["prompt"] = prompt
    if unique_keywords:
        transcription_cfg["keywords"] = unique_keywords
    if config.whisper_language:
        transcription_cfg["languages"] = [config.whisper_language]

    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": transcription_cfg,
                    "turn_detection": None,
                }
            },
        },
    }


class StreamingTranscriber:
    """Async client for streaming 24kHz audio to OpenAI Realtime transcription."""

    def __init__(self, config: Config, ws_url: str = REALTIME_WS_URL) -> None:
        self._config = config
        self._ws_url = ws_url
        self._ws: Any = None
        self._receive_task: asyncio.Task | None = None
        self._deltas: list[str] = []
        self._final_transcript: str = ""
        self._completed_event = asyncio.Event()
        self._last_error: Exception | None = None
        self._prompt: str = ""
        self._closed = False

    @property
    def accumulated_transcript(self) -> str:
        """Return the current in-memory accumulated transcript from delta events."""
        return "".join(self._deltas)

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected and open."""
        return self._ws is not None and not self._closed

    async def connect(self, context: AppContext | None = None) -> None:
        """Establish WebSocket connection with auth headers and send session.update."""
        if not self._config.openai_api_key:
            raise StreamingError("OpenAI API key not configured")

        headers = {
            "Authorization": f"Bearer {self._config.openai_api_key}",
        }

        sig = inspect.signature(websockets.connect)
        kw = "additional_headers" if "additional_headers" in sig.parameters else "extra_headers"
        connect_kwargs = {kw: headers}
        if "close_timeout" in sig.parameters:
            connect_kwargs["close_timeout"] = 0.2

        try:
            self._ws = await websockets.connect(self._ws_url, **connect_kwargs)
            log.info("Connected to OpenAI Realtime WebSocket: %s", self._ws_url)
        except Exception as e:
            raise StreamingError(f"Failed to connect to Realtime WebSocket: {e}") from e

        # Store prompt used for hallucination checks
        session_update_msg = build_session_update(self._config, context)
        transcription_cfg = (
            session_update_msg.get("session", {})
            .get("audio", {})
            .get("input", {})
            .get("transcription", {})
        )
        self._prompt = transcription_cfg.get("prompt", "")

        try:
            await self._ws.send(json.dumps(session_update_msg))
            log.debug("Sent session.update to Realtime WebSocket")
        except Exception as e:
            await self.close()
            raise StreamingError(f"Failed to send session.update: {e}") from e

        # Start background receive loop to buffer deltas in memory
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def send_audio_chunk(self, pcm_chunk: bytes) -> None:
        """Stream a 24kHz PCM16 chunk via input_audio_buffer.append."""
        if self._closed:
            return
        if self._ws is None:
            raise StreamingError("Cannot send audio chunk: WebSocket is not connected")
        if not pcm_chunk:
            return

        b64_audio = base64.b64encode(pcm_chunk).decode("ascii")
        msg = {
            "type": "input_audio_buffer.append",
            "audio": b64_audio,
        }
        await self._ws.send(json.dumps(msg))

    async def finish(self, timeout: float = 5.0) -> str:
        """Commit the audio buffer, wait for completion event, and return the transcript."""
        if self._ws is None or self._closed:
            raise StreamingError("Cannot finish turn: WebSocket is not connected")

        commit_msg = {"type": "input_audio_buffer.commit"}
        await self._ws.send(json.dumps(commit_msg))
        log.debug("Sent input_audio_buffer.commit")

        try:
            await asyncio.wait_for(self._completed_event.wait(), timeout=timeout)
        except TimeoutError:
            log.warning("Timed out waiting for transcription completion; using accumulated deltas")
            if not self._final_transcript:
                self._final_transcript = "".join(self._deltas)

        if self._last_error is not None:
            raise self._last_error

        transcript = self._final_transcript if self._final_transcript else "".join(self._deltas)
        transcript = transcript.strip()

        if self._prompt and is_prompt_hallucination(transcript, self._prompt):
            log.warning("Detected prompt hallucination on silence, suppressing transcript")
            transcript = ""

        # Mark closed immediately so is_connected is False and text paste is not blocked on TCP close
        self._closed = True
        ws_to_close = self._ws
        self._ws = None
        if self._receive_task is not None:
            self._receive_task.cancel()
            self._receive_task = None

        if ws_to_close is not None:
            async def _bg_close():
                try:
                    await ws_to_close.close()
                except Exception:
                    pass
            asyncio.create_task(_bg_close())

        log.info("Streaming transcript finalized: %s", transcript)
        return transcript

    async def close(self) -> None:
        """Close the WebSocket connection and terminate background tasks."""
        self._closed = True
        if self._receive_task is not None:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._ws is not None:
            ws = self._ws
            self._ws = None
            try:
                await ws.close()
            except Exception:
                pass

    async def _receive_loop(self) -> None:
        """Asynchronously receive WebSocket events and buffer transcript deltas in memory."""
        try:
            assert self._ws is not None
            async for raw_message in self._ws:
                try:
                    event = json.loads(raw_message)
                except Exception:
                    continue

                event_type = event.get("type")
                log.debug("Realtime WS event: %s", event_type)

                if event_type == "conversation.item.input_audio_transcription.delta":
                    delta = event.get("delta", "")
                    if delta:
                        # In-memory accumulation ONLY: strictly suppress external key injection
                        self._deltas.append(delta)

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript")
                    if transcript is not None:
                        self._final_transcript = transcript
                    else:
                        self._final_transcript = "".join(self._deltas)
                    self._completed_event.set()

                elif event_type == "error":
                    err_msg = event.get("error", {}).get("message", "Realtime API error")
                    log.error("Realtime WS error event: %s", err_msg)
                    self._last_error = StreamingError(f"Realtime API error: {err_msg}")
                    self._completed_event.set()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("Realtime WS receive loop error: %s", e)
            self._last_error = e
            self._completed_event.set()

    async def __aenter__(self) -> StreamingTranscriber:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
