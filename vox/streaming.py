"""OpenAI Realtime streaming transcription client over WebSockets."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Coroutine
from typing import Any

import websockets

from .config import Config
from .errors import StreamingError
from .transcribe import api_keywords, is_prompt_hallucination
from .window import AppContext

log = logging.getLogger(__name__)

REALTIME_WS_URL = "wss://api.openai.com/v1/realtime?intent=transcription"

# Keep references so background closes aren't garbage-collected mid-flight
_background: set[asyncio.Task] = set()


def _spawn(coro: Coroutine[Any, Any, Any]) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


def build_session_update(config: Config, context: AppContext | None = None) -> dict[str, Any]:
    """Build the session.update payload: the configured prompt and the same keywords batch mode sends."""
    transcription_cfg: dict[str, Any] = {
        "model": config.streaming_model or "gpt-live-transcribe",
    }
    if config.whisper_prompt:
        transcription_cfg["prompt"] = config.whisper_prompt
    keywords = api_keywords(config, context)
    if keywords:
        transcription_cfg["keywords"] = keywords
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
        self._completed = False  # the server sent the final transcript; later errors don't undo it
        self._echo_guard: str = ""
        self._closed = False

    @property
    def accumulated_transcript(self) -> str:
        """Return the current in-memory accumulated transcript from delta events."""
        return "".join(self._deltas)

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected and open."""
        return self._ws is not None and not self._closed

    @property
    def closed(self) -> bool:
        """Whether the session is over: finished, closed, or failed."""
        return self._closed

    async def connect(self, context: AppContext | None = None) -> None:
        """Establish WebSocket connection with auth headers and send session.update."""
        if not self._config.openai_api_key:
            raise StreamingError("OpenAI API key not configured")

        headers = {"Authorization": f"Bearer {self._config.openai_api_key}"}
        try:
            # The keepalive pings notice a dead connection, which is what bounds finish()
            self._ws = await websockets.connect(
                self._ws_url, additional_headers=headers, close_timeout=0.2, ping_interval=20, ping_timeout=20,
            )
            log.info("Connected to OpenAI Realtime WebSocket: %s", self._ws_url)
        except Exception as e:
            raise StreamingError(f"Failed to connect to Realtime WebSocket: {e}") from e

        session_update = build_session_update(self._config, context)
        transcription = session_update["session"]["audio"]["input"]["transcription"]
        # What an echo of the prompt on silence would repeat, as in batch mode
        self._echo_guard = "\n".join(
            filter(None, [transcription.get("prompt", ""), ", ".join(transcription.get("keywords", []))])
        )

        try:
            await self._ws.send(json.dumps(session_update))
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

    async def finish(self, timeout: float | None = None) -> str:
        """Commit the audio buffer and return the transcript once the server completes it.

        There is no short deadline: a long utterance can take a while, and the
        keepalive pings end the wait if the connection dies. ``timeout`` is only
        an optional outer bound. On a timeout, an error event, or a connection
        that closes first, this raises StreamingError, never a partial transcript.
        """
        if self._ws is None or self._closed:
            raise StreamingError("Cannot finish turn: WebSocket is not connected")

        try:
            await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            log.debug("Sent input_audio_buffer.commit")
            await asyncio.wait_for(self._completed_event.wait(), timeout)
        except TimeoutError as e:
            raise StreamingError("Timed out waiting for the transcription to complete") from e
        except websockets.ConnectionClosed as e:
            raise StreamingError(f"Realtime connection closed: {e}") from e
        finally:
            self._release()

        error = self._last_error
        if error is not None and not self._completed:
            if isinstance(error, StreamingError):
                raise error
            raise StreamingError(f"Realtime connection failed: {error}") from error

        transcript = self._final_transcript.strip()
        if self._echo_guard and is_prompt_hallucination(transcript, self._echo_guard):
            log.warning("Dropped a streaming transcript that only echoed the prompt")
            log.debug("Dropped echo: %s", transcript)
            return ""

        log.info("Streaming transcript: %d chars", len(transcript))
        log.debug("Streaming transcript: %s", transcript)
        return transcript

    def _release(self) -> None:
        """Mark the session over and drop the connection in the background, so the paste doesn't wait on the TCP close."""
        self._closed = True
        if self._receive_task is not None:
            self._receive_task.cancel()
            self._receive_task = None
        ws, self._ws = self._ws, None
        if ws is not None:
            _spawn(self._close_quietly(ws))

    @staticmethod
    async def _close_quietly(ws: Any) -> None:
        try:
            await ws.close()
        except Exception as e:
            log.debug("Error closing Realtime WebSocket: %s", e)

    async def close(self) -> None:
        """Close the WebSocket connection and terminate background tasks."""
        self._closed = True
        if not self._completed_event.is_set():
            # Wake anything still waiting in finish()
            self._last_error = StreamingError("Streaming session closed")
            self._completed_event.set()
        if self._receive_task is not None:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        ws, self._ws = self._ws, None
        if ws is not None:
            await self._close_quietly(ws)

    async def _receive_loop(self) -> None:
        """Asynchronously receive WebSocket events and buffer transcript deltas in memory."""
        try:
            async for raw_message in self._ws:
                try:
                    event = json.loads(raw_message)
                except ValueError:
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
                    self._final_transcript = transcript if transcript is not None else "".join(self._deltas)
                    self._completed = True
                    self._completed_event.set()

                elif event_type == "error" and not self._completed:
                    err_msg = event.get("error", {}).get("message", "Realtime API error")
                    log.error("Realtime WS error event: %s", err_msg)
                    self._last_error = StreamingError(f"Realtime API error: {err_msg}")
                    self._completed_event.set()

            if not self._completed_event.is_set():
                self._last_error = StreamingError("Realtime connection closed before the transcription completed")
                self._completed_event.set()
        except Exception as e:
            log.warning("Realtime WS receive loop error: %s", e)
            self._last_error = e
            self._completed_event.set()

    async def __aenter__(self) -> StreamingTranscriber:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
