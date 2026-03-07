"""Asyncio event loop and state machine orchestrator."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum

from .audio import Recorder, has_speech
from .config import Config, load_config
from .formatter import Formatter
from .hotkey import HotkeyListener
from .injector import inject_text
from .sounds import SoundPlayer
from .transcribe import Transcriber
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
    transcriber = Transcriber(config)
    formatter = Formatter(config)
    sounds = SoundPlayer(config)
    hotkey = HotkeyListener(config, loop, queue)

    hotkey.start()

    state = State.IDLE
    screen_capture_future: asyncio.Future | None = None
    recording_context = None
    log.info("Vox ready. Press %s to toggle recording.", config.hotkey)

    reload_task = asyncio.create_task(_config_reloader(config, formatter, sounds))

    try:
        while True:
            event = await queue.get()

            if event == "toggle":
                if state == State.IDLE:
                    state = State.RECORDING
                    sounds.play("start")

                    # Capture window context NOW and start OCR in background
                    recording_context = detect_active_window(config)
                    screen_capture_future = start_screen_capture(recording_context)

                    try:
                        recorder.start()
                        log.info("Recording...")
                    except Exception as e:
                        log.error("Failed to start recording: %s", e)
                        sounds.play("error")
                        state = State.IDLE
                        screen_capture_future = None

                elif state == State.RECORDING:
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
                        continue

                    # Re-detect window at stop time (user may have switched focus)
                    stop_context = detect_active_window(config)

                    asyncio.create_task(_process(
                        wav_data, config, transcriber, formatter, sounds,
                        stop_context, screen_capture_future,
                    ))
                    state = State.IDLE
                    screen_capture_future = None

                elif state == State.PROCESSING:
                    log.debug("Ignoring toggle during processing")
    except asyncio.CancelledError:
        pass
    finally:
        reload_task.cancel()
        hotkey.stop()


async def _process(
    wav_data: bytes,
    config: Config,
    transcriber: Transcriber,
    formatter: Formatter,
    sounds: SoundPlayer,
    context: AppContext,
    screen_capture_future: asyncio.Future | None,
) -> None:
    """Process recorded audio: transcribe, format, inject."""
    try:
        t0 = time.monotonic()

        # Get screen text from the background capture (started at recording time)
        if screen_capture_future is not None:
            try:
                screen_text = await screen_capture_future
                context.screen_text = screen_text
                log.debug("Screen context: %d chars", len(screen_text))
            except Exception as e:
                log.warning("Screen capture failed: %s", e)

        # Skip non-speech audio (avoids prompt leakage bug in gpt-4o-mini-transcribe)
        if not has_speech(wav_data):
            log.info("No speech detected, skipping transcription")
            return

        # Transcribe with context
        raw_text = await transcriber.transcribe(wav_data, context)
        if not raw_text:
            log.warning("Empty transcription result")
            sounds.play("error")
            return

        log.info("Transcript: %s", raw_text)

        # Format (skipped when skip_formatting is True)
        formatted = await formatter.format(raw_text, context)
        log.info("Formatted: %s", formatted)

        # Inject
        inject_text(formatted, context.app_type)

        elapsed = time.monotonic() - t0
        log.info("Done in %.1fs", elapsed)

    except Exception as e:
        log.error("Processing error: %s", e)
        sounds.play("error")


async def _config_reloader(config: Config, formatter: Formatter, sounds: SoundPlayer) -> None:
    """Poll config file mtime and reload hot-reloadable settings every 2s."""
    if config.config_path is None or not config.config_path.exists():
        return

    last_mtime = config.config_path.stat().st_mtime

    while True:
        await asyncio.sleep(2)
        try:
            if config.config_path is None or not config.config_path.exists():
                continue
            current_mtime = config.config_path.stat().st_mtime
            if current_mtime <= last_mtime:
                continue

            last_mtime = current_mtime
            new_config = load_config(config.config_path)

            config.snippets = new_config.snippets
            config.dictionary = new_config.dictionary
            config.styles = new_config.styles
            config.window_classes = new_config.window_classes
            config.sounds_enabled = new_config.sounds_enabled

            formatter._config = new_config
            sounds._enabled = new_config.sounds_enabled

            log.info("Config reloaded from %s", config.config_path)
        except Exception as e:
            log.warning("Config reload failed: %s", e)
