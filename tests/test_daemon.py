"""Integration and unit tests for daemon state machine, streaming integration, and batch fallback."""

import asyncio
import io
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


async def until(condition, timeout=2.0):
    """Wait for a condition instead of guessing a sleep."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


@pytest.fixture(autouse=True)
def _no_window_lookup():
    """On Linux the paste looks up the focused window again; keep that off the real display."""
    with patch("vox.daemon.detect_active_window", return_value=AppContext("", "", AppType.OTHER)):
        yield


@pytest.mark.anyio
async def test_daemon_streaming_process_success():
    """Streaming dictation: the live session is finalized once its audio is sent, and the transcript pasted."""
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

    audio_sent = asyncio.Event()

    async def send_last_audio():
        await audio_sent.wait()

    stream_task = asyncio.create_task(send_last_audio())

    async def finish():
        assert stream_task.done(), "committed before the last audio was sent"
        return "deploy Kubernetes cluster"

    mock_streaming = MagicMock()
    mock_streaming.finish = AsyncMock(side_effect=finish)
    mock_streaming.close = AsyncMock()

    with patch("vox.daemon.has_speech", return_value=True), \
         patch("vox.daemon.paste") as mock_paste:

        process = asyncio.create_task(_process(
            wav_data=wav_data,
            config=config,
            batch_transcriber=mock_batch,
            streaming_transcriber=mock_streaming,
            stream_task=stream_task,
            sounds=MagicMock(),
            queue=queue,
            context=context,
            screen_capture_future=None,
            mode="streaming",
        ))
        await asyncio.sleep(0.01)
        mock_streaming.finish.assert_not_called()  # still waiting for the worker
        audio_sent.set()
        await process

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
            mode="streaming",
        )

        # Verify expansion was pasted
        mock_paste.assert_called_once_with("alex@example.com", AppType.CHAT)


@pytest.mark.anyio
async def test_daemon_streaming_fallback_to_batch():
    """Verify graceful fallback to batch Transcriber when WebSocket streaming fails."""
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
    screen = asyncio.get_running_loop().create_future()
    screen.set_result("KubeClient handleRequest")

    with patch("vox.daemon.has_speech", return_value=True), \
         patch("vox.daemon.start_screen_capture", return_value=screen) as capture, \
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
            mode="streaming",
        )

        # Streaming should have been attempted, failed, and batch called
        mock_streaming.finish.assert_awaited_once()
        mock_streaming.close.assert_awaited()
        mock_batch.transcribe.assert_awaited_once_with(wav_data, context)
        # The live session never needed the screen; the fallback captures it then
        capture.assert_called_once_with(context)
        assert context.screen_text == "KubeClient handleRequest"

        # Verify paste was performed with batch result
        mock_paste.assert_called_once_with("transcribed via batch fallback", AppType.EDITOR)


@pytest.mark.anyio
async def test_daemon_explicit_batch_mode():
    """Verify that mode='batch' bypasses streaming entirely and uses Transcriber directly."""
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
            mode="batch",
        )

        mock_batch.transcribe.assert_awaited_once_with(wav_data, context)
        mock_paste.assert_called_once_with("direct batch text", AppType.TERMINAL)


@pytest.mark.anyio
async def test_daemon_silence_aborts_without_pasting():
    """Verify that silent audio turns cleanly abort without calling paste or transcription (batch mode only)."""
    config = Config(mode="batch")
    context = AppContext(
        wm_class="ghostty",
        window_title="Terminal",
        app_type=AppType.TERMINAL,
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    wav_data = _make_dummy_wav(0.5)

    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock()

    # A batch recording has no live session, only the screen capture started with it
    screen_capture_future = asyncio.get_running_loop().create_future()

    with patch("vox.daemon.has_speech", return_value=False), \
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
            screen_capture_future=screen_capture_future,
            mode="batch",
        )

        mock_batch.transcribe.assert_not_called()
        mock_paste.assert_not_called()
        assert screen_capture_future.cancelled()
        assert queue.get_nowait() == "process_done"
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
    mock_transcriber.closed = False
    mock_transcriber.connect = AsyncMock()
    mock_transcriber.send_audio_chunk = AsyncMock()

    context = AppContext(wm_class="code", window_title="Code", app_type=AppType.EDITOR)
    await _stream_worker(mock_recorder, mock_transcriber, context)

    mock_transcriber.connect.assert_awaited_once_with(context)
    assert mock_transcriber.send_audio_chunk.await_count == 2
    mock_transcriber.send_audio_chunk.assert_any_await(chunk1)
    mock_transcriber.send_audio_chunk.assert_any_await(chunk2)


async def fake_reloader(*args, **kwargs):
    try:
        await asyncio.sleep(100)
    except asyncio.CancelledError:
        pass


@pytest.mark.anyio
async def test_daemon_cancel_during_recording():
    """Verify cancel during RECORDING stops audio, discards frames, cancels streaming/screen, restores volume, plays cancel sound, and resets to IDLE."""
    config = Config(mode="streaming", openai_api_key="test", sounds_enabled=True, attenuation_enabled=True, attenuation_level=0.5)
    config._config_path = None

    queue_holder = {}
    mock_hotkey = MagicMock()

    def fake_hotkey_init(cfg, loop, queue):
        queue_holder["queue"] = queue
        return mock_hotkey

    mock_recorder = MagicMock()
    mock_recorder.discard = MagicMock()

    async def fake_stream_chunks():
        while True:
            await asyncio.sleep(1.0)
            yield b"\x00" * 100

    mock_recorder.stream_chunks = fake_stream_chunks

    mock_sounds = MagicMock()
    mock_streaming = MagicMock()
    mock_streaming.closed = False
    mock_streaming.connect = AsyncMock()
    mock_streaming.close = AsyncMock()

    loop = asyncio.get_running_loop()
    fake_screen_future = loop.create_future()

    with patch("vox.daemon.HotkeyListener", side_effect=fake_hotkey_init), \
         patch("vox.daemon.Recorder", return_value=mock_recorder), \
         patch("vox.daemon.SoundPlayer", return_value=mock_sounds), \
         patch("vox.daemon.Transcriber"), \
         patch("vox.daemon.StreamingTranscriber", return_value=mock_streaming), \
         patch("vox.daemon._config_reloader", side_effect=fake_reloader), \
         patch("vox.daemon.start_screen_capture", return_value=fake_screen_future) as capture, \
         patch("vox.daemon.detect_active_window", return_value=AppContext(wm_class="term", window_title="Term", app_type=AppType.TERMINAL)), \
         patch("vox.daemon.get_volume", return_value=0.8), \
         patch("vox.daemon.set_volume") as mock_set_volume:

        from vox.daemon import _main
        main_task = asyncio.create_task(_main(config))
        await until(lambda: "queue" in queue_holder)

        queue = queue_holder["queue"]

        # 1. Start recording with toggle
        await queue.put("toggle")
        await until(lambda: mock_streaming.connect.await_count == 1)

        mock_sounds.play.assert_any_call("start")
        mock_set_volume.assert_called_with(0.4)  # 0.8 * 0.5
        assert mock_recorder.start.call_args.kwargs["stream"] is True
        # Streaming sends its keywords when it connects, before a capture could finish: none is started
        capture.assert_not_called()

        # 2. Cancel recording
        await queue.put("cancel")
        await until(lambda: mock_streaming.close.await_count >= 1)

        # Discarded audio frames
        mock_recorder.discard.assert_called_once()
        # Volume restored to 0.8
        mock_set_volume.assert_called_with(0.8)
        # Cancel sound played
        mock_sounds.play.assert_any_call("cancel")
        # Streaming transcriber closed
        mock_streaming.close.assert_awaited()

        # 3. Verify state reset to IDLE: a subsequent toggle starts recording again
        mock_sounds.reset_mock()
        await queue.put("toggle")
        await until(lambda: mock_sounds.play.call_count >= 1)
        mock_sounds.play.assert_any_call("start")

        main_task.cancel()
        try:
            await main_task
        except asyncio.CancelledError:
            pass


@pytest.mark.anyio
async def test_daemon_cancel_during_processing():
    """Verify cancel during PROCESSING cancels process task, suppresses paste, plays cancel sound, and resets to IDLE."""
    config = Config(mode="batch", openai_api_key="test", sounds_enabled=True, attenuation_enabled=True, attenuation_level=0.5)
    config._config_path = None

    queue_holder = {}
    mock_hotkey = MagicMock()

    def fake_hotkey_init(cfg, loop, queue):
        queue_holder["queue"] = queue
        return mock_hotkey

    mock_recorder = MagicMock()
    mock_recorder.stop = MagicMock(return_value=b"fake_wav")

    mock_sounds = MagicMock()
    process_started = asyncio.Event()
    process_cancelled = asyncio.Event()

    async def fake_process(**kwargs):
        process_started.set()
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            process_cancelled.set()
            raise

    with patch("vox.daemon.HotkeyListener", side_effect=fake_hotkey_init), \
         patch("vox.daemon.Recorder", return_value=mock_recorder), \
         patch("vox.daemon.SoundPlayer", return_value=mock_sounds), \
         patch("vox.daemon.Transcriber"), \
         patch("vox.daemon._config_reloader", side_effect=fake_reloader), \
         patch("vox.daemon.detect_active_window", return_value=AppContext(wm_class="term", window_title="Term", app_type=AppType.TERMINAL)), \
         patch("vox.daemon.start_screen_capture", return_value=None), \
         patch("vox.daemon.get_volume", return_value=0.8), \
         patch("vox.daemon.set_volume") as mock_set_volume, \
         patch("vox.daemon._process", side_effect=fake_process), \
         patch("vox.daemon.paste") as mock_paste:

        from vox.daemon import _main
        main_task = asyncio.create_task(_main(config))
        await until(lambda: "queue" in queue_holder)

        queue = queue_holder["queue"]

        # Start recording
        await queue.put("toggle")
        await until(lambda: mock_recorder.start.called)

        # Stop recording -> transitions to PROCESSING
        await queue.put("toggle")
        await process_started.wait()

        # Cancel while PROCESSING
        await queue.put("cancel")
        await until(process_cancelled.is_set)

        await until(lambda: ("cancel",) in [c.args for c in mock_sounds.play.call_args_list])
        mock_paste.assert_not_called()
        mock_set_volume.assert_called_with(0.8)  # restored when the recording stopped

        # Subsequent toggle starts recording (state was reset to IDLE)
        mock_sounds.reset_mock()
        await queue.put("toggle")
        await until(lambda: mock_sounds.play.call_count >= 1)
        mock_sounds.play.assert_any_call("start")

        main_task.cancel()
        try:
            await main_task
        except asyncio.CancelledError:
            pass


@pytest.mark.anyio
async def test_process_cancellation_suppresses_paste_and_cleans_resources():
    """Verify _process cancelled midway cancels stream_task/screen_capture, closes transcriber, and does not paste or error sound."""
    config = Config(mode="streaming")
    context = AppContext(wm_class="code", window_title="Code", app_type=AppType.EDITOR)
    queue: asyncio.Queue[str] = asyncio.Queue()
    wav_data = _make_dummy_wav(0.5)

    mock_sounds = MagicMock()
    mock_streaming = MagicMock()
    # Hang on finish to allow cancellation while waiting
    finish_started = asyncio.Event()

    async def slow_finish(timeout=3.0):
        finish_started.set()
        await asyncio.sleep(10.0)
        return "transcribed text"

    mock_streaming.finish = AsyncMock(side_effect=slow_finish)
    mock_streaming.close = AsyncMock()

    loop = asyncio.get_running_loop()
    screen_capture_future = loop.create_future()
    stream_task = asyncio.create_task(asyncio.sleep(0))  # the worker ends once the recorder stops

    with patch("vox.daemon.has_speech", return_value=True), \
         patch("vox.daemon.paste") as mock_paste:

        process_task = asyncio.create_task(_process(
            wav_data=wav_data,
            config=config,
            batch_transcriber=MagicMock(),
            streaming_transcriber=mock_streaming,
            stream_task=stream_task,
            sounds=mock_sounds,
            queue=queue,
            context=context,
            screen_capture_future=screen_capture_future,
            mode="streaming",
        ))

        await finish_started.wait()
        process_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await process_task

        mock_paste.assert_not_called()
        mock_sounds.play.assert_not_called()
        mock_streaming.close.assert_awaited()
        assert screen_capture_future.cancelled()
        assert stream_task.done()


@pytest.mark.anyio
async def test_process_cancelled_while_the_stream_worker_drains():
    """Cancelling while the last audio is still being sent stops the worker and never commits."""
    config = Config(mode="streaming")
    context = AppContext(wm_class="code", window_title="Code", app_type=AppType.EDITOR)
    mock_streaming = MagicMock()
    mock_streaming.finish = AsyncMock(return_value="text")
    mock_streaming.close = AsyncMock()
    sending = asyncio.Event()

    async def draining():
        sending.set()
        await asyncio.sleep(10.0)

    stream_task = asyncio.create_task(draining())
    queue: asyncio.Queue[str] = asyncio.Queue()

    with patch("vox.daemon.paste") as mock_paste:
        process_task = asyncio.create_task(_process(
            wav_data=_make_dummy_wav(0.5), config=config, batch_transcriber=MagicMock(),
            streaming_transcriber=mock_streaming, stream_task=stream_task, sounds=MagicMock(),
            queue=queue, context=context, screen_capture_future=None, mode="streaming",
        ))
        await sending.wait()
        await asyncio.sleep(0.01)
        process_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await process_task

    assert stream_task.cancelled()
    mock_streaming.finish.assert_not_called()
    mock_streaming.close.assert_awaited()
    mock_paste.assert_not_called()
    assert queue.get_nowait() == "process_done"


@pytest.mark.anyio
async def test_daemon_cancel_ignored_in_idle():
    """Verify cancel event received while in IDLE state is safely ignored without playing cancel sound."""
    config = Config(openai_api_key="test")
    config._config_path = None

    queue_holder = {}
    mock_hotkey = MagicMock()

    def fake_hotkey_init(cfg, loop, queue):
        queue_holder["queue"] = queue
        return mock_hotkey

    mock_sounds = MagicMock()

    with patch("vox.daemon.HotkeyListener", side_effect=fake_hotkey_init), \
         patch("vox.daemon.Recorder"), \
         patch("vox.daemon.Transcriber"), \
         patch("vox.daemon._config_reloader", side_effect=fake_reloader), \
         patch("vox.daemon.SoundPlayer", return_value=mock_sounds):

        from vox.daemon import _main
        main_task = asyncio.create_task(_main(config))
        await until(lambda: "queue" in queue_holder)

        queue = queue_holder["queue"]

        # Cancel in IDLE
        await queue.put("cancel")
        await until(queue.empty)
        await asyncio.sleep(0.02)

        # Cancel sound should not be played
        mock_sounds.play.assert_not_called()

        main_task.cancel()
        try:
            await main_task
        except asyncio.CancelledError:
            pass



@pytest.mark.anyio
async def test_streaming_bypasses_vad_gate():
    """Streaming mode must not consult the local VAD; it never drops audio as 'no speech'."""
    config = Config(mode="streaming")
    context = AppContext(wm_class="ghostty", window_title="Terminal", app_type=AppType.TERMINAL)
    queue: asyncio.Queue[str] = asyncio.Queue()
    wav_data = _make_dummy_wav(0.5)

    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock()
    mock_streaming = MagicMock()
    mock_streaming.finish = AsyncMock(return_value="streamed text")
    mock_streaming.close = AsyncMock()

    with patch("vox.daemon.has_speech", return_value=False) as mock_vad, \
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
            mode="streaming",
        )

        mock_vad.assert_not_called()
        mock_streaming.finish.assert_awaited_once()
        mock_batch.transcribe.assert_not_called()
        mock_paste.assert_called_once_with("streamed text", AppType.TERMINAL)


@pytest.mark.anyio
async def test_process_records_injected_text_in_history(tmp_path):
    """A successful paste is persisted with app type, audio duration, and the mode actually used."""
    from vox.history import HistoryDB

    history = HistoryDB(tmp_path / "history.db")
    config = Config(mode="batch", snippets={"my email": "alex@example.com"})
    context = AppContext(wm_class="slack", window_title="Slack", app_type=AppType.CHAT)
    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock(return_value="my email.")

    with patch("vox.daemon.has_speech", return_value=True), patch("vox.daemon.paste"):
        await _process(
            wav_data=_make_dummy_wav(0.5),
            config=config,
            batch_transcriber=mock_batch,
            streaming_transcriber=None,
            stream_task=None,
            sounds=MagicMock(),
            queue=asyncio.Queue(),
            context=context,
            screen_capture_future=None,
            mode="batch",
            history=history,
        )

    [rec] = history.search()
    assert rec.text == "alex@example.com"  # stores what was injected, after snippet expansion
    assert rec.app_type == "CHAT"
    assert rec.duration_seconds == pytest.approx(0.5)
    assert rec.transcription_mode == "batch"
    history.close()


@pytest.mark.anyio
@pytest.mark.parametrize("transcript", ["", "   "])
async def test_process_skips_history_for_empty_transcript(tmp_path, transcript):
    from vox.history import HistoryDB

    history = HistoryDB(tmp_path / "history.db")
    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock(return_value=transcript)

    with patch("vox.daemon.has_speech", return_value=True), patch("vox.daemon.paste") as mock_paste:
        await _process(
            wav_data=_make_dummy_wav(0.5),
            config=Config(mode="batch"),
            batch_transcriber=mock_batch,
            streaming_transcriber=None,
            stream_task=None,
            sounds=MagicMock(),
            queue=asyncio.Queue(),
            context=AppContext(wm_class="code", window_title="VSCode", app_type=AppType.EDITOR),
            screen_capture_future=None,
            mode="batch",
            history=history,
        )

    mock_paste.assert_not_called()
    assert history.search() == []
    history.close()


@pytest.mark.anyio
async def test_process_keeps_history_when_paste_fails(tmp_path):
    """The transcription is billed and the text is not on screen: history is where the user can copy it."""
    from vox.errors import InjectionError
    from vox.history import HistoryDB

    history = HistoryDB(tmp_path / "history.db")
    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock(return_value="hello")
    sounds = MagicMock()

    with patch("vox.daemon.has_speech", return_value=True), \
         patch("vox.daemon.paste", side_effect=InjectionError("no focus")):
        await _process(
            wav_data=_make_dummy_wav(0.5),
            config=Config(mode="batch"),
            batch_transcriber=mock_batch,
            streaming_transcriber=None,
            stream_task=None,
            sounds=sounds,
            queue=asyncio.Queue(),
            context=AppContext(wm_class="code", window_title="VSCode", app_type=AppType.EDITOR),
            screen_capture_future=None,
            mode="batch",
            history=history,
        )

    sounds.play.assert_called_with("error")
    [rec] = history.search()
    assert rec.text == "hello"
    history.close()


@pytest.mark.anyio
async def test_history_write_failure_does_not_play_error(tmp_path):
    """The paste already happened, so a broken history DB must not surface as a dictation error."""
    history = MagicMock()
    history.insert.side_effect = RuntimeError("disk full")
    mock_batch = MagicMock()
    mock_batch.transcribe = AsyncMock(return_value="hello")
    sounds = MagicMock()

    with patch("vox.daemon.has_speech", return_value=True), patch("vox.daemon.paste") as mock_paste:
        await _process(
            wav_data=_make_dummy_wav(0.5),
            config=Config(mode="batch"),
            batch_transcriber=mock_batch,
            streaming_transcriber=None,
            stream_task=None,
            sounds=sounds,
            queue=asyncio.Queue(),
            context=AppContext(wm_class="code", window_title="VSCode", app_type=AppType.EDITOR),
            screen_capture_future=None,
            mode="batch",
            history=history,
        )

    mock_paste.assert_called_once()
    history.insert.assert_called_once()
    sounds.play.assert_not_called()
