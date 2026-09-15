"""Integration and unit tests for daemon state machine, streaming integration, and batch fallback."""

import asyncio
import io
import time
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from vox.config import Config
from vox.daemon import _process, _stream_worker
from vox.errors import StreamingError
from vox.window import AppContext, AppType


def _make_dummy_wav(duration_s: float = 0.5, sample_rate: int = 48000) -> bytes:
    """Generate a dummy PCM16 WAV byte string."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        samples = (np.sin(np.linspace(0, 100, int(sample_rate * duration_s))) * 10000).astype(np.int16)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


@pytest.mark.anyio
async def test_daemon_streaming_process_success_and_sub_200ms_latency():
    """Verify streaming dictation: end-of-turn finalization, sub-200ms latency, and paste injection."""
    config = Config(
        mode="streaming",
        snippets={"my email": "alex@example.com"},
    )
    context = AppContext(
        wm_class="ghostty",
        window_title="Ghostty Terminal",
        app_type=AppType.TERMINAL,
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    wav_data = _make_dummy_wav(0.5)

    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock(return_value="batch fallback text")

    mock_streaming = MagicMock()
    # Simulate low-latency WebSocket finish (< 50ms)
    async def fast_finish(timeout=3.0):
        await asyncio.sleep(0.03)  # 30ms finalization
        return "deploy Kubernetes cluster"

    mock_streaming.finish = AsyncMock(side_effect=fast_finish)
    mock_streaming.close = AsyncMock()

    # Pre-completed stream task
    stream_task = asyncio.create_task(asyncio.sleep(0.01))

    with patch("vox.daemon.has_speech", return_value=True), \
         patch("vox.daemon.paste") as mock_paste:

        t_start = time.monotonic()
        await _process(
            wav_data=wav_data,
            config=config,
            batch_transcriber=mock_batch,
            streaming_transcriber=mock_streaming,
            stream_task=stream_task,
            sounds=MagicMock(),
            queue=queue,
            context=context,
            screen_capture_future=None,
        )
        elapsed_s = time.monotonic() - t_start

        # Verify sub-200ms latency requirement
        assert elapsed_s < 0.20, f"Latency {elapsed_s:.3f}s exceeded 200ms threshold"

        # Verify streaming transcriber was finalized and batch was not used
        mock_streaming.finish.assert_awaited_once()
        mock_batch.transcribe.assert_not_called()

        # Verify single-shot paste injection
        mock_paste.assert_called_once_with("deploy Kubernetes cluster", AppType.TERMINAL)

        # Verify completion signal in queue
        event = await queue.get()
        assert event == "process_done"


@pytest.mark.anyio
async def test_daemon_streaming_snippet_expansion():
    """Verify that completed streaming transcript undergoes snippet expansion before pasting."""
    config = Config(
        mode="streaming",
        snippets={"my email": "alex@example.com"},
    )
    context = AppContext(
        wm_class="slack",
        window_title="Slack",
        app_type=AppType.CHAT,
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    wav_data = _make_dummy_wav(0.5)

    mock_streaming = MagicMock()
    mock_streaming.finish = AsyncMock(return_value="my email.")
    mock_streaming.close = AsyncMock()

    with patch("vox.daemon.has_speech", return_value=True), \
         patch("vox.daemon.paste") as mock_paste:

        await _process(
            wav_data=wav_data,
            config=config,
            batch_transcriber=MagicMock(),
            streaming_transcriber=mock_streaming,
            stream_task=None,
            sounds=MagicMock(),
            queue=queue,
            context=context,
            screen_capture_future=None,
        )

        # Verify expansion was pasted
        mock_paste.assert_called_once_with("alex@example.com", AppType.CHAT)


@pytest.mark.anyio
async def test_daemon_streaming_fallback_to_batch():
    """Verify graceful fallback to batch WhisperTranscriber when WebSocket streaming fails."""
    config = Config(mode="streaming")
    context = AppContext(
        wm_class="code",
        window_title="VSCode",
        app_type=AppType.EDITOR,
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    wav_data = _make_dummy_wav(0.5)

    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock(return_value="transcribed via batch fallback")

    mock_streaming = MagicMock()
    mock_streaming.finish = AsyncMock(side_effect=StreamingError("Connection lost: WebSocket reset"))
    mock_streaming.close = AsyncMock()

    with patch("vox.daemon.has_speech", return_value=True), \
         patch("vox.daemon.paste") as mock_paste:

        await _process(
            wav_data=wav_data,
            config=config,
            batch_transcriber=mock_batch,
            streaming_transcriber=mock_streaming,
            stream_task=None,
            sounds=MagicMock(),
            queue=queue,
            context=context,
            screen_capture_future=None,
        )

        # Streaming should have been attempted, failed, and batch called
        mock_streaming.finish.assert_awaited_once()
        mock_batch.transcribe.assert_awaited_once_with(wav_data, context)

        # Verify paste was performed with batch result
        mock_paste.assert_called_once_with("transcribed via batch fallback", AppType.EDITOR)


@pytest.mark.anyio
async def test_daemon_explicit_batch_mode():
    """Verify that mode='batch' bypasses streaming entirely and uses WhisperTranscriber directly."""
    config = Config(mode="batch")
    context = AppContext(
        wm_class="ghostty",
        window_title="Terminal",
        app_type=AppType.TERMINAL,
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    wav_data = _make_dummy_wav(0.5)

    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock(return_value="direct batch text")

    with patch("vox.daemon.has_speech", return_value=True), \
         patch("vox.daemon.paste") as mock_paste:

        await _process(
            wav_data=wav_data,
            config=config,
            batch_transcriber=mock_batch,
            streaming_transcriber=None,
            stream_task=None,
            sounds=MagicMock(),
            queue=queue,
            context=context,
            screen_capture_future=None,
        )

        mock_batch.transcribe.assert_awaited_once_with(wav_data, context)
        mock_paste.assert_called_once_with("direct batch text", AppType.TERMINAL)


@pytest.mark.anyio
async def test_daemon_silence_aborts_without_pasting():
    """Verify that silent audio turns cleanly abort without calling paste or transcription."""
    config = Config(mode="streaming")
    context = AppContext(
        wm_class="ghostty",
        window_title="Terminal",
        app_type=AppType.TERMINAL,
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    wav_data = _make_dummy_wav(0.5)

    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock()

    mock_streaming = MagicMock()
    mock_streaming.finish = AsyncMock()
    mock_streaming.close = AsyncMock()

    # Active stream task and screen capture future
    stream_task = asyncio.create_task(asyncio.sleep(10.0))
    loop = asyncio.get_running_loop()
    screen_capture_future = loop.create_future()

    with patch("vox.daemon.has_speech", return_value=False), \
         patch("vox.daemon.paste") as mock_paste:

        await _process(
            wav_data=wav_data,
            config=config,
            batch_transcriber=mock_batch,
            streaming_transcriber=mock_streaming,
            stream_task=stream_task,
            sounds=MagicMock(),
            queue=queue,
            context=context,
            screen_capture_future=screen_capture_future,
        )

        mock_streaming.finish.assert_not_called()
        mock_streaming.close.assert_awaited_once()
        mock_batch.transcribe.assert_not_called()
        mock_paste.assert_not_called()
        assert stream_task.cancelled()
        assert screen_capture_future.cancelled()


@pytest.mark.anyio
async def test_stream_worker_pushes_chunks():
    """Verify _stream_worker connects and forwards chunks from Recorder to StreamingTranscriber."""
    mock_recorder = MagicMock()
    chunk1 = b"\x01\x00" * 2400
    chunk2 = b"\x02\x00" * 2400

    async def mock_stream_chunks():
        yield chunk1
        yield chunk2

    mock_recorder.stream_chunks = mock_stream_chunks

    mock_transcriber = MagicMock()
    mock_transcriber.connect = AsyncMock()
    mock_transcriber.send_audio_chunk = AsyncMock()

    context = AppContext(wm_class="code", window_title="Code", app_type=AppType.EDITOR)
    await _stream_worker(mock_recorder, mock_transcriber, context)

    mock_transcriber.connect.assert_awaited_once_with(context)
    assert mock_transcriber.send_audio_chunk.await_count == 2
    mock_transcriber.send_audio_chunk.assert_any_await(chunk1)
    mock_transcriber.send_audio_chunk.assert_any_await(chunk2)
