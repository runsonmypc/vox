"""Unit and integration tests for StreamingTranscriber with mock WebSocket server."""

import asyncio
import base64
import json

import pytest
import websockets

from vox.config import Config
from vox.errors import StreamingError
from vox.streaming import StreamingTranscriber, build_session_update
from vox.window import AppContext, AppType


def test_build_session_update():
    config = Config(
        streaming_model="gpt-live-transcribe",
        whisper_prompt="Base prompt hint",
        whisper_language="en",
        dictionary=["PostgreSQL", "FastAPI", "Kubernetes"],
    )
    context = AppContext(
        wm_class="code",
        window_title="VSCode - test_streaming.py",
        app_type=AppType.EDITOR,
        screen_text="def test_something(): Kubernetes deployment",
    )

    update = build_session_update(config, context)
    assert update["type"] == "session.update"

    session = update["session"]
    assert session["type"] == "transcription"
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["input"]["turn_detection"] is None

    transcription = session["audio"]["input"]["transcription"]
    assert transcription["model"] == "gpt-live-transcribe"
    assert transcription["languages"] == ["en"]
    assert "Base prompt hint" in transcription["prompt"]
    assert "VSCode" in transcription["prompt"]
    assert "PostgreSQL" in transcription["keywords"]
    assert "FastAPI" in transcription["keywords"]
    assert "Kubernetes" in transcription["keywords"]


@pytest.mark.anyio
async def test_streaming_transcriber_missing_api_key():
    config = Config(openai_api_key="")
    transcriber = StreamingTranscriber(config)
    with pytest.raises(StreamingError, match="API key not configured"):
        await transcriber.connect()


@pytest.mark.anyio
async def test_streaming_session_lifecycle():
    """Verify session.update, chunk streaming, delta accumulation, and commit finalization."""
    received_messages = []
    server_ready = asyncio.Event()

    async def mock_ws_handler(websocket):
        server_ready.set()
        try:
            async for message in websocket:
                data = json.loads(message)
                received_messages.append(data)

                msg_type = data.get("type")
                if msg_type == "input_audio_buffer.append":
                    # Respond with a delta
                    delta_event = {
                        "type": "conversation.item.input_audio_transcription.delta",
                        "delta": "chunk ",
                    }
                    await websocket.send(json.dumps(delta_event))

                elif msg_type == "input_audio_buffer.commit":
                    # Respond with completion event
                    completed_event = {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "chunk chunk finalized transcript",
                    }
                    await websocket.send(json.dumps(completed_event))
        except Exception:
            pass

    async with websockets.serve(mock_ws_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ws_url = f"ws://127.0.0.1:{port}"

        config = Config(
            openai_api_key="test-sk-12345",
            streaming_model="gpt-live-transcribe",
            dictionary=["VoxTest"],
        )
        context = AppContext(
            wm_class="slack",
            window_title="Slack #engineering",
            app_type=AppType.CHAT,
        )

        transcriber = StreamingTranscriber(config, ws_url=ws_url)
        await transcriber.connect(context)
        assert transcriber.is_connected

        # 1. Verify session.update was sent immediately
        await asyncio.sleep(0.05)
        assert len(received_messages) >= 1
        first_msg = received_messages[0]
        assert first_msg["type"] == "session.update"
        tx_cfg = first_msg["session"]["audio"]["input"]["transcription"]
        assert tx_cfg["model"] == "gpt-live-transcribe"
        assert "VoxTest" in tx_cfg["keywords"]

        # 2. Stream two chunks via input_audio_buffer.append
        raw_pcm1 = b"\x01\x00" * 2400
        raw_pcm2 = b"\x02\x00" * 2400

        await transcriber.send_audio_chunk(raw_pcm1)
        await transcriber.send_audio_chunk(raw_pcm2)

        # Allow receive loop to process deltas
        await asyncio.sleep(0.05)

        # Verify messages sent to server
        append_msgs = [m for m in received_messages if m["type"] == "input_audio_buffer.append"]
        assert len(append_msgs) == 2
        assert append_msgs[0]["audio"] == base64.b64encode(raw_pcm1).decode("ascii")
        assert append_msgs[1]["audio"] == base64.b64encode(raw_pcm2).decode("ascii")

        # Verify in-memory delta accumulation
        assert transcriber.accumulated_transcript == "chunk chunk "

        # 3. Finalize turn via commit and await completion
        result = await transcriber.finish(timeout=2.0)

        # Verify commit message was received by server
        commit_msgs = [m for m in received_messages if m["type"] == "input_audio_buffer.commit"]
        assert len(commit_msgs) == 1

        # Verify final transcript returned
        assert result == "chunk chunk finalized transcript"
        assert not transcriber.is_connected


@pytest.mark.anyio
async def test_streaming_server_error_handling():
    """Verify that a server error event is handled and raises StreamingError on finish."""
    async def mock_ws_error_handler(websocket):
        try:
            async for message in websocket:
                data = json.loads(message)
                if data.get("type") == "session.update":
                    error_event = {
                        "type": "error",
                        "error": {"message": "Model not available"},
                    }
                    await websocket.send(json.dumps(error_event))
        except Exception:
            pass

    async with websockets.serve(mock_ws_error_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ws_url = f"ws://127.0.0.1:{port}"

        config = Config(openai_api_key="sk-test")
        transcriber = StreamingTranscriber(config, ws_url=ws_url)
        await transcriber.connect()
        await asyncio.sleep(0.05)

        with pytest.raises(StreamingError, match="Model not available"):
            await transcriber.finish(timeout=1.0)


@pytest.mark.anyio
async def test_streaming_prompt_hallucination_suppressed():
    """Verify that when the transcript echoes back the prompt on silence, it is suppressed."""
    async def mock_ws_handler(websocket):
        try:
            async for message in websocket:
                data = json.loads(message)
                if data.get("type") == "input_audio_buffer.commit":
                    # Echo back prompt words
                    completed_event = {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "PostgreSQL, FastAPI, Kubernetes",
                    }
                    await websocket.send(json.dumps(completed_event))
        except Exception:
            pass

    async with websockets.serve(mock_ws_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ws_url = f"ws://127.0.0.1:{port}"

        config = Config(
            openai_api_key="sk-test",
            whisper_prompt="PostgreSQL, FastAPI, Kubernetes",
        )
        transcriber = StreamingTranscriber(config, ws_url=ws_url)
        await transcriber.connect()
        result = await transcriber.finish(timeout=1.0)
        assert result == ""
