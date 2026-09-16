"""Asyncio event loop and state machine orchestrator."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum

from .attenuation import get_volume, set_volume
from .audio import Recorder, has_speech
from .config import Config, load_config
from .hotkey import HotkeyListener
from .injector import inject_text, paste
from .sounds import SoundPlayer
from .streaming import StreamingTranscriber
from .transcribe import WhisperTranscriber
from .window import AppContext, detect_active_window, start_screen_capture

log = logging.getLogger(__name__)


class State(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    PROCESSING = "PROCESSING"


def run(config: Config) -> None:
    """Entry point — run the asyncio event loop."""
    asyncio.run(_main(config))


async def _main(config: Config) -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()

    recorder = Recorder(config)
    batch_transcriber = WhisperTranscriber(config)
    sounds = SoundPlayer(config)
    hotkey = HotkeyListener(config, loop, queue)

    hotkey.start()

    state = State.IDLE
    process_task: asyncio.Task | None = None
    stream_task: asyncio.Task | None = None
    streaming_transcriber: StreamingTranscriber | None = None
    screen_capture_future: asyncio.Future | None = None
    recording_context: AppContext | None = None
    saved_volume: float | None = None
    log.info("Vox ready (%s mode). Press %s to toggle recording.", config.mode, config.hotkey)

    reload_task = asyncio.create_task(_config_reloader(config, sounds))

    try:
        while True:
            event = await queue.get()

            if event == "toggle":
                if state == State.IDLE:
                    state = State.RECORDING
                    sounds.play("start", blocking=True)

                    # Capture window context NOW and start OCR in background
                    recording_context = detect_active_window(config)
                    screen_capture_future = start_screen_capture(recording_context)

                    try:
                        recorder.start(loop=loop)
                        if config.attenuation_enabled:
                            saved_volume = get_volume()
                            if saved_volume is not None:
                                set_volume(saved_volume * config.attenuation_level)
                    except Exception as e:
                        log.error("Failed to start recording: %s", e)
                        if saved_volume is not None:
                            set_volume(saved_volume)
                            saved_volume = None
                        sounds.play("error")
                        state = State.IDLE
                        screen_capture_future = None
                        continue

                    # If streaming mode, initiate streaming connection and chunk worker
                    if config.mode == "streaming":
                        streaming_transcriber = StreamingTranscriber(config)
                        stream_task = asyncio.create_task(
                            _stream_worker(recorder, streaming_transcriber, recording_context)
                        )
                    else:
                        streaming_transcriber = None
                        stream_task = None

                elif state == State.RECORDING:
                    if saved_volume is not None:
                        set_volume(saved_volume)
                        saved_volume = None

                    state = State.PROCESSING
                    sounds.play("stop")
                    log.info("Processing...")

                    try:
                        wav_data = recorder.stop()
                    except Exception as e:
                        log.error("Failed to stop recording: %s", e)
                        sounds.play("error")
                        state = State.IDLE
                        screen_capture_future = None
                        if stream_task is not None:
                            stream_task.cancel()
                            stream_task = None
                        if streaming_transcriber is not None:
                            asyncio.create_task(streaming_transcriber.close())
                            streaming_transcriber = None
                        continue

                    # Re-detect window at stop time (user may have switched focus)
                    stop_context = detect_active_window(config)

                    process_task = asyncio.create_task(_process(
                        wav_data=wav_data,
                        config=config,
                        batch_transcriber=batch_transcriber,
                        streaming_transcriber=streaming_transcriber,
                        stream_task=stream_task,
                        sounds=sounds,
                        queue=queue,
                        context=stop_context,
                        screen_capture_future=screen_capture_future,
                    ))
                    streaming_transcriber = None
                    stream_task = None
                    screen_capture_future = None

                elif state == State.PROCESSING:
                    log.info("Ignoring toggle during processing")
                    sounds.play("busy")

            elif event == "process_done":
                if state == State.PROCESSING:
                    state = State.IDLE
                    process_task = None
                    log.info("Processing complete, ready")
    except asyncio.CancelledError:
        pass
    finally:
        reload_task.cancel()
        hotkey.stop()


async def _stream_worker(
    recorder: Recorder,
    streaming_transcriber: StreamingTranscriber,
    context: AppContext | None,
) -> None:
    """Stream audio chunks live to StreamingTranscriber during recording."""
    try:
        await streaming_transcriber.connect(context)
        async for chunk in recorder.stream_chunks():
            if getattr(streaming_transcriber, "_closed", False) is True:
                break
            await streaming_transcriber.send_audio_chunk(chunk)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        if getattr(streaming_transcriber, "_closed", False) is not True:
            log.warning("Live audio streaming encountered error: %s (will fall back to batch Whisper)", e)


async def _process(
    wav_data: bytes,
    config: Config,
    batch_transcriber: WhisperTranscriber,
    streaming_transcriber: StreamingTranscriber | None,
    stream_task: asyncio.Task | None,
    sounds: SoundPlayer,
    queue: asyncio.Queue[str],
    context: AppContext,
    screen_capture_future: asyncio.Future | None,
) -> None:
    """Process recorded audio: finalize streaming or transcribe via batch, then inject."""
    try:
        t0 = time.monotonic()

        # Check speech FIRST before waiting on background OCR
        if not has_speech(wav_data):
            log.info("No speech detected, skipping transcription")
            if screen_capture_future is not None and not screen_capture_future.done():
                screen_capture_future.cancel()
            if stream_task is not None:
                stream_task.cancel()
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass
            if streaming_transcriber is not None:
                await streaming_transcriber.close()
            return

        # Attach screen text if background capture is ready
        if screen_capture_future is not None:
            try:
                if config.mode == "streaming" and streaming_transcriber is not None:
                    if screen_capture_future.done():
                        context.screen_text = screen_capture_future.result()
                else:
                    context.screen_text = await asyncio.wait_for(screen_capture_future, timeout=1.5)
                if context.screen_text:
                    log.debug("Screen context: %d chars", len(context.screen_text))
            except Exception as e:
                log.debug("Screen capture not ready or failed: %s", e)

        text = ""
        used_streaming = False

        # Attempt streaming transcription if configured
        if config.mode == "streaming" and streaming_transcriber is not None:
            try:
                if stream_task is not None:
                    # Allow stream worker up to 1.5s to finish pushing final buffered chunks
                    try:
                        await asyncio.wait_for(stream_task, timeout=1.5)
                    except asyncio.TimeoutError:
                        log.warning("Stream worker timed out pushing chunks")

                text = await streaming_transcriber.finish(timeout=3.0)
                used_streaming = True
                log.info("Streaming transcription succeeded in %.3fs", time.monotonic() - t0)
            except Exception as e:
                log.warning("Streaming transcription failed (%s: %s), falling back to batch Whisper", type(e).__name__, e)
                try:
                    await streaming_transcriber.close()
                except Exception:
                    pass

        # Fallback to batch Whisper if streaming was not used or failed
        if not used_streaming:
            log.info("Using batch Whisper transcription")
            text = await batch_transcriber.transcribe(wav_data, context)

        if not text or not text.strip():
            log.info("Empty transcription result, skipping injection")
            return

        # Check for snippet expansion (exact phrase match)
        text_clean = text.strip().rstrip(".?!,").lower()
        for trigger, expansion in config.snippets.items():
            if text_clean == trigger.strip().rstrip(".?!,").lower():
                log.info("Snippet match: %r -> %r", trigger, expansion)
                text = expansion
                break

        # Single-shot paste injection via paste / inject_text
        paste(text, context.app_type)

        elapsed = time.monotonic() - t0
        log.info("Done in %.3fs (%s)", elapsed, "streaming" if used_streaming else "batch")

    except Exception as e:
        log.error("Processing error: %s", e)
        sounds.play("error")
    finally:
        queue.put_nowait("process_done")


async def _config_reloader(config: Config, sounds: SoundPlayer) -> None:
    """Poll config file mtime and reload hot-reloadable settings every 2s."""
    if config.config_path is None:
        return

    last_mtime: float = 0
    try:
        last_mtime = config.config_path.stat().st_mtime
    except OSError:
        pass

    while True:
        await asyncio.sleep(2)
        try:
            try:
                current_mtime = config.config_path.stat().st_mtime
            except OSError:
                continue
            if current_mtime <= last_mtime:
                continue

            last_mtime = current_mtime
            new_config = load_config(config.config_path)

            config.snippets = new_config.snippets
            config.dictionary = new_config.dictionary
            config.styles = new_config.styles
            config.window_classes = new_config.window_classes
            config.sounds_enabled = new_config.sounds_enabled
            config.attenuation_enabled = new_config.attenuation_enabled
            config.attenuation_level = new_config.attenuation_level
            config.mode = new_config.mode
            config.streaming_model = new_config.streaming_model
            config.audio_device = new_config.audio_device

            sounds._enabled = new_config.sounds_enabled

            log.info("Config reloaded from %s", config.config_path)
        except Exception as e:
            log.warning("Config reload failed: %s", e)
