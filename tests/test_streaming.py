"""Unit and integration tests for StreamingTranscriber with mock WebSocket server."""

import asyncio
import base64
import json
import logging

import pytest
import websockets

from vox.config import Config
from vox.errors import StreamingError
from vox.streaming import StreamingTranscriber, build_session_update
from vox.window import AppContext, AppType

COMPLETED = "conversation.item.input_audio_transcription.completed"


async def until(condition, timeout=2.0):
    """Wait for a condition instead of guessing a sleep."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


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
    # The window title and screen reach the model as keywords, as in batch mode, never as raw prompt text
    assert transcription["prompt"] == "Base prompt hint"
    assert "VSCode" in transcription["keywords"]
    assert "PostgreSQL" in transcription["keywords"]
    assert "FastAPI" in transcription["keywords"]
    assert "Kubernetes" in transcription["keywords"]


def test_session_keywords_are_filtered_and_capped():
    config = Config(dictionary=[f"Term{i}" for i in range(45)] + ["<b>bold</b>"])
    transcription = build_session_update(config)["session"]["audio"]["input"]["transcription"]
    assert len(transcription["keywords"]) == 40
    assert not any("<" in word or ">" in word for word in transcription["keywords"])
    assert "prompt" not in transcription


def test_session_update_without_screen_context_has_only_the_dictionary():
    config = Config(dictionary=["Vox"], context_screen=False)
    context = AppContext(
        wm_class="code",
        window_title="SecretProject - Visual Studio Code",
        app_type=AppType.EDITOR,
        screen_text="def handleRequest(): pass",
    )
    transcription = build_session_update(config, context)["session"]["audio"]["input"]["transcription"]
    assert transcription["keywords"] == ["Vox"]
    assert "SecretProject" not in json.dumps(transcription)


@pytest.mark.anyio
async def test_streaming_transcriber_missing_api_key():
    config = Config(openai_api_key="")
    transcriber = StreamingTranscriber(config)
    with pytest.raises(StreamingError, match="API key not configured"):
        await transcriber.connect()


@pytest.mark.anyio
async def test_streaming_session_lifecycle(caplog):
    """Verify session.update, chunk streaming, delta accumulation, and commit finalization."""
    received_messages = []
    auth_headers = []

    async def mock_ws_handler(websocket):
        auth_headers.append(websocket.request.headers.get("Authorization"))
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
                        "type": COMPLETED,
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
        assert not transcriber.closed

        # 1. Verify session.update was sent immediately, with the key as a bearer token
        await until(lambda: len(received_messages) >= 1)
        assert auth_headers == ["Bearer test-sk-12345"]
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
        await until(lambda: transcriber.accumulated_transcript == "chunk chunk ")

        # Verify messages sent to server
        append_msgs = [m for m in received_messages if m["type"] == "input_audio_buffer.append"]
        assert len(append_msgs) == 2
        assert append_msgs[0]["audio"] == base64.b64encode(raw_pcm1).decode("ascii")
        assert append_msgs[1]["audio"] == base64.b64encode(raw_pcm2).decode("ascii")

        # 3. Finalize turn via commit and await completion
        with caplog.at_level(logging.INFO, logger="vox.streaming"):
            result = await transcriber.finish()

        # Verify commit message was received by server
        commit_msgs = [m for m in received_messages if m["type"] == "input_audio_buffer.commit"]
        assert len(commit_msgs) == 1

        # Verify final transcript returned
        assert result == "chunk chunk finalized transcript"
        assert not transcriber.is_connected
        assert transcriber.closed
        # The text itself is only logged at DEBUG
        assert "Streaming transcript: 32 chars" in caplog.text
        assert "finalized" not in caplog.text

        # Audio after the end is dropped, not an error
        await transcriber.send_audio_chunk(raw_pcm1)


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
        await until(lambda: transcriber._last_error is not None)

        with pytest.raises(StreamingError, match="Model not available"):
            await transcriber.finish(timeout=1.0)
        assert transcriber.closed


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
                        "type": COMPLETED,
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


async def _session(handler):
    """A connected transcriber against a local server running ``handler``; returns (server, transcriber)."""
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    transcriber = StreamingTranscriber(Config(openai_api_key="sk-test"), ws_url=f"ws://127.0.0.1:{port}")
    await transcriber.connect()
    return server, transcriber


@pytest.mark.anyio
async def test_finish_with_a_timeout_raises_instead_of_returning_a_partial_transcript():
    async def silent_after_delta(websocket):
        async for message in websocket:
            if json.loads(message).get("type") == "input_audio_buffer.append":
                await websocket.send(json.dumps({
                    "type": "conversation.item.input_audio_transcription.delta", "delta": "half a sent",
                }))

    server, transcriber = await _session(silent_after_delta)
    try:
        await transcriber.send_audio_chunk(b"\x00\x00" * 100)
        await until(lambda: transcriber.accumulated_transcript)
        with pytest.raises(StreamingError, match="Timed out"):
            await transcriber.finish(timeout=0.1)
        assert transcriber.closed
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_a_connection_that_closes_before_completion_is_an_error():
    async def hang_up_on_commit(websocket):
        async for message in websocket:
            if json.loads(message).get("type") == "input_audio_buffer.commit":
                await websocket.close()

    server, transcriber = await _session(hang_up_on_commit)
    try:
        with pytest.raises(StreamingError, match="closed"):
            await transcriber.finish()
        assert transcriber.closed
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_an_error_after_completion_does_not_discard_the_transcript():
    async def complete_then_error(websocket):
        async for message in websocket:
            if json.loads(message).get("type") == "input_audio_buffer.commit":
                await websocket.send(json.dumps({"type": COMPLETED, "transcript": "all done"}))
                await websocket.send(json.dumps({"type": "error", "error": {"message": "late"}}))

    server, transcriber = await _session(complete_then_error)
    try:
        assert await transcriber.finish() == "all done"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_close_wakes_a_waiting_finish():
    async def never_completes(websocket):
        async for _ in websocket:
            pass

    server, transcriber = await _session(never_completes)
    try:
        waiting = asyncio.create_task(transcriber.finish())
        await asyncio.sleep(0.05)
        assert not waiting.done()
        await transcriber.close()
        with pytest.raises(StreamingError, match="closed"):
            await asyncio.wait_for(waiting, 2)
        assert transcriber.closed
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_finish_after_close_raises():
    async def idle(websocket):
        async for _ in websocket:
            pass

    server, transcriber = await _session(idle)
    try:
        await transcriber.close()
        with pytest.raises(StreamingError, match="not connected"):
            await transcriber.finish()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_a_server_error_ends_the_session_while_the_socket_stays_open():
    received = []

    async def error_on_first_chunk(websocket):
        async for message in websocket:
            received.append(json.loads(message)["type"])
            if received[-1] == "input_audio_buffer.append":
                await websocket.send(json.dumps({"type": "error", "error": {"message": "Rate limit"}}))

    server, transcriber = await _session(error_on_first_chunk)
    try:
        await transcriber.send_audio_chunk(b"\x00\x00" * 100)
        await until(lambda: transcriber.closed)
        assert transcriber._ws is not None  # the server kept the connection open

        await transcriber.send_audio_chunk(b"\x00\x00" * 100)  # dropped: nothing more to bill
        with pytest.raises(StreamingError, match="Rate limit"):
            await transcriber.finish()
        await asyncio.sleep(0.05)
        assert received == ["session.update", "input_audio_buffer.append"]  # and no commit
    finally:
        server.close()
        await server.wait_closed()
