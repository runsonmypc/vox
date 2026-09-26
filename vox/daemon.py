"""Asyncio event loop and state machine orchestrator."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave
from enum import Enum
from typing import TYPE_CHECKING

from .attenuation import get_volume, set_volume
from .audio import Recorder, has_speech
from .config import DEFAULT_CONFIG_PATH, Config, load_config, update_transcription_mode
from .errors import ConfigError
from .history import HistoryDB
from .hotkey import HotkeyListener
from .injector import inject_text, paste
from .keystore import KeystoreError, get_api_key
from .sounds import SoundPlayer
from .streaming import StreamingTranscriber
from .transcribe import WhisperTranscriber
from .whisper_cpp import WhisperCppTranscriber
from .window import AppContext, detect_active_window, start_screen_capture

if TYPE_CHECKING:
    from .ui.tray import TrayManager

log = logging.getLogger(__name__)


class State(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    PROCESSING = "PROCESSING"


def run(config: Config) -> None:
    """Entry point — run the asyncio event loop.

    With a tray icon, the tray owns the main thread (Cocoa and GTK want their
    event loop there) and the event loop runs in a worker thread. Otherwise the
    event loop runs on the main thread.
    """
    from .ui.tray import create_tray

    tray = create_tray(config)
    if tray is None and config.uses_openai and not config.openai_api_key:
        # Nowhere to ask for a key; headless Vox is started by hand, not by a service that would retry
        reason = f" ({config.api_key_error})" if config.api_key_error else ""
        log.error(
            "No OpenAI API key%s. Start Vox from the desktop and choose Set API Key… from its menu, "
            "or set OPENAI_API_KEY.", reason,
        )
        raise SystemExit(1)
    if tray is None:
        asyncio.run(_main(config))
        return

    errors: list[BaseException] = []

    def daemon() -> None:
        try:
            asyncio.run(_main(config, tray))
        except BaseException as e:
            errors.append(e)
        finally:
            tray.stop()

    thread = threading.Thread(target=daemon, name="vox-daemon", daemon=True)
    thread.start()
    tray.run()
    tray.request_quit()  # no-op if the daemon already exited
    thread.join(timeout=5)
    if errors:
        raise errors[0]


async def _main(config: Config, tray: TrayManager | None = None) -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()

    recorder = Recorder(config)
    recorder.warmup()
    batch_transcriber = (
        WhisperCppTranscriber(config) if config.mode == "whisper_cpp" else WhisperTranscriber(config)
    )
    sounds = SoundPlayer(config)
    hotkey = HotkeyListener(config, loop, queue)
    history = _open_history()

    hotkey.start()

    state = State.IDLE
    paused = False
    process_task: asyncio.Task | None = None
    stream_task: asyncio.Task | None = None
    streaming_transcriber: StreamingTranscriber | None = None
    screen_capture_future: asyncio.Future | None = None
    recording_context: AppContext | None = None
    saved_volume: float | None = None
    log.info("Vox ready (%s mode). Press %s to toggle recording.", config.mode, config.hotkey)

    def set_state(new_state: State) -> None:
        nonlocal state
        state = new_state
        if tray is not None:
            tray.set_state(new_state.value)

    async def reload_api_key() -> None:
        """Re-read the key off the event loop, since a locked keyring may be waiting on an unlock prompt."""
        try:
            config.openai_api_key = await loop.run_in_executor(None, get_api_key)
            config.api_key_error = None
        except KeystoreError as e:
            config.api_key_error = str(e)  # keep any key already in hand
            log.warning("Couldn't read the OpenAI API key from the keychain: %s", e)
        if tray is not None:
            tray.key_changed()

    def key_missing() -> bool:
        return config.uses_openai and not config.openai_api_key

    if tray is not None:
        tray.attach(loop, queue, history, asyncio.current_task())
        if key_missing() and config.api_key_error is None:
            tray.open_key_window()

    reload_task = asyncio.create_task(_config_reloader(config, sounds, recorder))

    try:
        while True:
            event = await queue.get()

            if event.startswith("mode:"):
                mode = event.partition(":")[2]
                if state is not State.IDLE or mode == config.mode:
                    continue
                try:
                    if mode not in ("batch", "streaming", "whisper_cpp"):
                        raise ConfigError(f"Invalid transcription mode: {mode}")
                    if mode != "whisper_cpp" and not config.openai_api_key:
                        raise ConfigError("Set an OpenAI API key before selecting OpenAI transcription")
                    candidate = (
                        WhisperCppTranscriber(config) if mode == "whisper_cpp" else WhisperTranscriber(config)
                    )
                    update_transcription_mode(config.config_path or DEFAULT_CONFIG_PATH, mode)
                except (ConfigError, OSError, ValueError) as e:
                    log.warning("Could not switch transcription mode: %s", e)
                    sounds.play("error")
                    continue
                config.mode = mode
                batch_transcriber = candidate
                log.info("Transcription mode set to %s", mode)
                if tray is not None:
                    tray.mode_changed()
                continue

            if event == "api_key":  # the key window closed
                await reload_api_key()
                log.info("OpenAI API key %s", "set" if config.openai_api_key else "not set")
                continue

            if event in ("pause", "resume"):
                paused = event == "pause"
                log.info("Dictation %s", "paused" if paused else "resumed")
                if tray is not None:
                    tray.set_paused(paused)
                if paused and state == State.RECORDING:
                    event = "cancel"  # discard the in-progress recording and release the mic
                else:
                    sounds.play(event)
                    continue
            elif paused and event in ("toggle", "cancel"):
                log.info("Ignoring %s while paused", event)
                if event == "toggle":
                    sounds.play("busy")
                continue

            if event == "toggle":
                if state == State.IDLE and key_missing():
                    await reload_api_key()  # it may have been saved, or the keyring unlocked, since
                if state == State.IDLE and key_missing():
                    sounds.play("error")
                    if config.api_key_error is not None:
                        log.warning("Not recording: the keychain couldn't be read (%s)", config.api_key_error)
                    else:
                        log.warning("Not recording: no OpenAI API key. Choose Set API Key… from the Vox menu.")
                        if tray is not None:
                            tray.open_key_window()
                    continue
                if state == State.IDLE:
                    set_state(State.RECORDING)

                    # 1. Start audio recording IMMEDIATELY with zero startup latency
                    try:
                        recorder.start(loop=loop)
                    except Exception as e:
                        log.error("Failed to start recording: %s", e)
                        sounds.play("error")
                        set_state(State.IDLE)
                        continue

                    # 2. Play start audio feedback non-blocking
                    sounds.play("start", blocking=False)

                    # 3. Attenuate volume
                    if config.attenuation_enabled:
                        saved_volume = get_volume()
                        if saved_volume is not None:
                            set_volume(saved_volume * config.attenuation_level)

                    # 4. Capture window context and screen OCR in background
                    recording_context = detect_active_window(config)
                    screen_capture_future = start_screen_capture(recording_context)

                    # 5. If streaming mode, initiate streaming connection and chunk worker
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

                    set_state(State.PROCESSING)
                    sounds.play("stop", blocking=False)
                    log.info("Processing...")

                    # Post-roll buffer (120ms) to ensure trailing words/syllables are not cut off
                    await asyncio.sleep(0.12)

                    try:
                        wav_data = recorder.stop()
                    except Exception as e:
                        log.error("Failed to stop recording: %s", e)
                        sounds.play("error")
                        set_state(State.IDLE)
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

                    try:
                        if config.mode == "whisper_cpp" and not isinstance(batch_transcriber, WhisperCppTranscriber):
                            batch_transcriber = WhisperCppTranscriber(config)
                        elif config.mode != "whisper_cpp" and isinstance(batch_transcriber, WhisperCppTranscriber):
                            batch_transcriber = WhisperTranscriber(config)
                    except Exception as e:
                        log.error("Transcriber configuration error: %s", e)
                        sounds.play("error")
                        set_state(State.IDLE)
                        if screen_capture_future is not None and not screen_capture_future.done():
                            screen_capture_future.cancel()
                        screen_capture_future = None
                        continue

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
                        history=history,
                        tray=tray,
                    ))
                    streaming_transcriber = None
                    stream_task = None
                    screen_capture_future = None

                elif state == State.PROCESSING:
                    log.info("Ignoring toggle during processing")
                    sounds.play("busy")

            elif event == "cancel":
                if state == State.RECORDING:
                    log.info("Cancelling active recording...")
                    if saved_volume is not None:
                        set_volume(saved_volume)
                        saved_volume = None

                    try:
                        recorder.discard()
                    except AttributeError:
                        try:
                            recorder.stop()
                        except Exception:
                            pass
                    except Exception as e:
                        log.debug("Error discarding recording: %s", e)

                    if screen_capture_future is not None and not screen_capture_future.done():
                        screen_capture_future.cancel()
                    screen_capture_future = None

                    if stream_task is not None:
                        stream_task.cancel()
                        stream_task = None
                    if streaming_transcriber is not None:
                        asyncio.create_task(streaming_transcriber.close())
                        streaming_transcriber = None

                    recording_context = None
                    sounds.play("cancel")
                    set_state(State.IDLE)
                    log.info("Recording cancelled, state reset to IDLE")

                elif state == State.PROCESSING:
                    log.info("Cancelling in-flight processing...")
                    if saved_volume is not None:
                        set_volume(saved_volume)
                        saved_volume = None

                    if process_task is not None and not process_task.done():
                        process_task.cancel()
                    process_task = None

                    if stream_task is not None:
                        stream_task.cancel()
                        stream_task = None
                    if streaming_transcriber is not None:
                        asyncio.create_task(streaming_transcriber.close())
                        streaming_transcriber = None

                    recording_context = None
                    sounds.play("cancel")
                    set_state(State.IDLE)
                    log.info("Processing cancelled, state reset to IDLE")

                elif state == State.IDLE:
                    log.debug("Ignoring cancel event in IDLE state")

            elif event == "process_done":
                if state == State.PROCESSING:
                    set_state(State.IDLE)
                    process_task = None
                    log.info("Processing complete, ready")
    except asyncio.CancelledError:
        pass
    finally:
        reload_task.cancel()
        hotkey.stop()
        recorder.close()
        if history is not None:
            history.close()
        if tray is not None:
            tray.stop()


def _open_history() -> HistoryDB | None:
    try:
        return HistoryDB()
    except Exception as e:
        log.warning("Dictation history disabled: %s", e)
        return None


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
    batch_transcriber: WhisperTranscriber | WhisperCppTranscriber,
    streaming_transcriber: StreamingTranscriber | None,
    stream_task: asyncio.Task | None,
    sounds: SoundPlayer,
    queue: asyncio.Queue[str],
    context: AppContext,
    screen_capture_future: asyncio.Future | None,
    history: HistoryDB | None = None,
    tray: TrayManager | None = None,
) -> None:
    """Process recorded audio: finalize streaming or transcribe via batch, then inject."""
    try:
        t0 = time.monotonic()

        # VAD gate only for batch mode. Streaming has its own silence/hallucination
        # guard and the local VAD produces false negatives that drop real speech.
        use_streaming = config.mode == "streaming" and streaming_transcriber is not None
        if not use_streaming and streaming_transcriber is not None:
            if stream_task is not None:
                stream_task.cancel()
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass
            await streaming_transcriber.close()
            streaming_transcriber = None
            stream_task = None
        if not use_streaming and not has_speech(wav_data):
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
            log.info("Using %s transcription", "whisper.cpp" if config.mode == "whisper_cpp" else "batch Whisper")
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

        if history is not None:
            saved = await _record_history(history, text, context, wav_data,
                                          "streaming" if used_streaming else config.mode)
            if saved and tray is not None:
                tray.history_changed()

        elapsed = time.monotonic() - t0
        log.info("Done in %.3fs (%s)", elapsed, "streaming" if used_streaming else config.mode)

    except asyncio.CancelledError:
        log.info("Processing task cancelled")
        if screen_capture_future is not None and not screen_capture_future.done():
            screen_capture_future.cancel()
        if stream_task is not None:
            stream_task.cancel()
        if streaming_transcriber is not None:
            try:
                await streaming_transcriber.close()
            except Exception:
                pass
        raise
    except Exception as e:
        log.error("Processing error: %s", e)
        sounds.play("error")
    finally:
        queue.put_nowait("process_done")


async def _record_history(
    history: HistoryDB,
    text: str,
    context: AppContext,
    wav_data: bytes,
    transcription_mode: str,
) -> bool:
    """Persist an injected dictation. Failures are logged, never raised: the paste already happened."""
    try:
        return await asyncio.to_thread(
            history.insert,
            text,
            app_type=context.app_type.value,
            duration_seconds=_wav_duration(wav_data),
            transcription_mode=transcription_mode,
        ) is not None
    except Exception as e:
        log.warning("Failed to save dictation history: %s", e)
        return False


def _wav_duration(wav_data: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return None


async def _config_reloader(config: Config, sounds: SoundPlayer, recorder: Recorder | None = None) -> None:
    """Poll config file mtime and reload hot-reloadable settings every 2s."""
    if config.config_path is None:
        return

    last_mtime: float = 0
    try:
        last_mtime = config.config_path.stat().st_mtime
    except OSError:
        pass
    file_audio = (config.audio_device, config.sample_rate)

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
            config.whisper_model = new_config.whisper_model
            config.whisper_cpp_binary = new_config.whisper_cpp_binary
            config.whisper_cpp_model = new_config.whisper_cpp_model
            config.whisper_language = new_config.whisper_language
            config.whisper_prompt = new_config.whisper_prompt

            # Apply audio settings only when the file changed them, so a device picked
            # from the menu bar survives unrelated edits such as vocabulary changes.
            new_file_audio = (new_config.audio_device, new_config.sample_rate)
            if new_file_audio != file_audio:
                file_audio = new_file_audio
                config.audio_device, config.sample_rate = new_file_audio
                if recorder is not None:
                    recorder.reconfigure(config)

            sounds._enabled = new_config.sounds_enabled

            log.info("Config reloaded from %s", config.config_path)
        except Exception as e:
            log.warning("Config reload failed: %s", e)
