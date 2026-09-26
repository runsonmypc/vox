"""Asyncio event loop and state machine orchestrator."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import threading
import time
import wave
from collections.abc import Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from .attenuation import get_volume, set_volume
from .audio import Recorder, has_speech, is_digital_silence
from .config import (
    DEFAULT_CONFIG_PATH,
    Config,
    load_config,
    snippet_key,
    update_max_recording_seconds,
    update_transcription_mode,
)
from .errors import ConfigError, DependencyError, InjectionError, StreamingError, VoxError
from .history import HistoryDB
from .hotkey import HotkeyListener
from .injector import check_accessibility_permission, paste
from .keystore import KeystoreError, get_api_key
from .sounds import SoundPlayer
from .streaming import StreamingTranscriber
from .transcribe import PartialTranscriptionError, WhisperTranscriber
from .whisper_cpp import WhisperCppTranscriber
from .window import AppContext, detect_active_window, start_screen_capture

if TYPE_CHECKING:
    from .ui.tray import TrayManager

log = logging.getLogger(__name__)

MODES = ("batch", "streaming", "whisper_cpp")

WAYLAND_NOTICE = "Wayland: hotkey and paste only work in X11 apps"
SILENT_MIC_NOTICE = "Microphone is silent: check its permission"
ACCESSIBILITY_NOTICE = "Accessibility access needed"

# Trailing audio kept after the stop key, so the last syllable isn't cut off
_POST_ROLL_SECONDS = 0.12
# How long a batch transcription waits for the screen capture started with the recording
_SCREEN_WAIT_SECONDS = 1.5
# The device re-scan restarts PortAudio, which on Linux also plays Vox's sounds: let them finish first
_DEVICE_REFRESH_DELAY = 1.0
_CONFIG_POLL_SECONDS = 2.0


class State(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    PROCESSING = "PROCESSING"


# Keep references so fire-and-forget tasks aren't garbage-collected mid-flight
_background: set[asyncio.Task] = set()


def _spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


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

    notice = _platform_notice()
    if notice == WAYLAND_NOTICE:
        log.warning(
            "This is a Wayland session. Vox's hotkey, window detection and paste use X11, so they only "
            "reach X11 (XWayland) apps; native Wayland windows ignore them. For full support, log in "
            "with an Xorg session (e.g. 'GNOME on Xorg')."
        )
    if tray is None:
        asyncio.run(_main(config))
        return
    if notice is not None:
        tray.set_notice(notice)

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


def _platform_notice() -> str | None:
    """A lasting platform problem for the tray's status line, if there is one."""
    if sys.platform == "darwin":
        return None if check_accessibility_permission(prompt=False) else ACCESSIBILITY_NOTICE
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return WAYLAND_NOTICE
    return None


def _attenuate(level: float) -> float | None:
    """Scale the output volume down; return the level to restore, or None if it can't be controlled."""
    volume = get_volume()
    if volume is not None:
        set_volume(volume * level)
    return volume


@dataclass
class _Session:
    """What one recording holds open until its transcription ends."""

    mode: str
    limit_seconds: int
    streaming: StreamingTranscriber | None = None
    stream_task: asyncio.Task | None = None
    screen_future: asyncio.Future | None = None

    def release(self) -> None:
        """Drop the live transcription and screen capture. Safe to repeat."""
        if self.screen_future is not None and not self.screen_future.done():
            self.screen_future.cancel()
        if self.stream_task is not None:
            self.stream_task.cancel()
        if self.streaming is not None:
            _spawn(self.streaming.close())
        self.streaming = self.stream_task = self.screen_future = None


async def _main(config: Config, tray: TrayManager | None = None) -> None:
    await _Daemon(config, tray).run()


class _Daemon:
    """The dictation state machine: events from the hotkey, the recorder and the tray arrive on one queue."""

    def __init__(self, config: Config, tray: TrayManager | None) -> None:
        self.config = config
        self.tray = tray
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        try:
            self.hotkey = HotkeyListener(config, self.loop, self.queue)
        except DependencyError as e:
            log.error("%s", e)
            raise SystemExit(1) from e
        self.recorder = Recorder(config)
        self.recorder.warmup()
        self.sounds = SoundPlayer(config)
        self.history = _open_history()

        self.state = State.IDLE
        self.paused = False
        self.saved_volume: float | None = None
        # Lowering the volume runs on a worker thread; a quit that lands meanwhile waits for it, then restores
        self._volume_lock = threading.Lock()
        self._closing = False
        self.session: _Session | None = None  # the recording in progress
        self.inflight: _Session | None = None  # the recording being transcribed
        self.process_task: asyncio.Task | None = None
        self.mic_silent = False

        self.batch_transcriber: WhisperTranscriber | WhisperCppTranscriber | None = None
        self.transcriber_local = False
        try:
            self._use_transcriber(self._build_transcriber(config.mode), config.mode)
            config.mode_error = None
        except ConfigError as e:
            config.mode_error = str(e)  # the tray shows it; a toggle tries again
            log.error("%s", e)

        self._devices_sent: list[tuple[int, str]] | None = None
        self._device_timer: asyncio.TimerHandle | None = None
        self._device_job: asyncio.Task | None = None

    # -- Loop ---------------------------------------------------------------

    async def run(self) -> None:
        config, tray = self.config, self.tray
        self.hotkey.start()
        log.info("Vox ready (%s mode). Press %s to toggle recording.", config.mode, config.hotkey)
        if tray is not None:
            tray.attach(self.loop, self.queue, self.history, asyncio.current_task())
            if self._key_missing() and config.api_key_error is None:
                tray.open_key_window()
        reload_task = asyncio.create_task(_config_reloader(config, self.sounds, self.recorder))
        self._schedule_device_refresh(0)

        try:
            while True:
                event = await self.queue.get()
                await self._settle_devices()
                try:
                    await self._handle(event)
                except Exception:
                    log.exception("Unexpected error handling %r", event)
                    await self._recover()
        except asyncio.CancelledError:
            pass
        finally:
            self._shutdown(reload_task)

    async def _handle(self, event: str) -> None:
        if event.startswith("mode:"):
            await self._switch_mode(event.partition(":")[2])
            return
        if event.startswith("limit:"):
            self._set_limit(event.partition(":")[2])
            return
        if event == "api_key":  # the key window closed
            await self._reload_api_key()
            log.info("OpenAI API key %s", "set" if self.config.openai_api_key else "not set")
            return

        if event in ("pause", "resume"):
            self.paused = event == "pause"
            log.info("Dictation %s", "paused" if self.paused else "resumed")
            if self.tray is not None:
                self.tray.set_paused(self.paused)
            if not (self.paused and self.state is State.RECORDING):
                self.sounds.play(event)
                return
            event = "cancel"  # discard the in-progress recording and release the mic
        elif self.paused and event in ("toggle", "cancel"):
            log.info("Ignoring %s while paused", event)
            if event == "toggle":
                self.sounds.play("busy")
            return

        if event == "toggle":
            if self.state is State.IDLE:
                await self._start_recording()
            elif self.state is State.RECORDING:
                await self._stop_recording()
            else:
                log.info("Ignoring toggle during processing")
                self.sounds.play("busy")
        elif event == "limit":
            # Only for this recording: a stale event from a cancelled one finds limit_reached reset
            if self.state is State.RECORDING and self.session is not None and self.recorder.limit_reached:
                log.info("Recording limit reached (%d s)", self.session.limit_seconds)
                await self._stop_recording()
        elif event == "cancel":
            await self._cancel()
        elif event == "process_done":
            # A cancelled task's late event must not end a newer task's processing
            if self.state is State.PROCESSING and (self.process_task is None or self.process_task.done()):
                self._end_processing()
                self.set_state(State.IDLE)
                log.info("Processing complete, ready")
        else:
            log.debug("Ignoring unknown event %r", event)

    def set_state(self, state: State) -> None:
        self.state = state
        if self.tray is not None:
            self.tray.set_state(state.value)
        if state is State.IDLE:
            self._schedule_device_refresh(_DEVICE_REFRESH_DELAY)

    # -- Recording ------------------------------------------------------------

    async def _start_recording(self) -> None:
        if not await self._ready_to_record():
            return
        config = self.config
        self.set_state(State.RECORDING)
        session = self.session = _Session(mode=config.mode, limit_seconds=config.max_recording_seconds)

        # 1. Start audio recording IMMEDIATELY with zero startup latency
        try:
            self.recorder.start(
                loop=self.loop,
                stream=session.mode == "streaming",
                on_limit=lambda: self.queue.put_nowait("limit"),
            )
        except Exception as e:
            log.error("Failed to start recording: %s", e, exc_info=not isinstance(e, VoxError))
            self.sounds.play("error")
            self.session = None
            self.set_state(State.IDLE)
            return

        # 2. Play start audio feedback non-blocking
        self.sounds.play("start", blocking=False)

        # 3. Attenuate volume
        if config.attenuation_enabled:
            await asyncio.to_thread(self._lower_volume, config.attenuation_level)

        # 4. Window context, and the screen capture that runs while the user speaks. Streaming sends
        #    its keywords when it connects, before any capture could finish, so it doesn't start one.
        context = await asyncio.to_thread(detect_active_window, config)
        if config.context_screen and session.mode != "streaming":
            session.screen_future = start_screen_capture(context)

        # 5. If streaming mode, initiate streaming connection and chunk worker
        if session.mode == "streaming":
            session.streaming = StreamingTranscriber(config)
            session.stream_task = asyncio.create_task(_stream_worker(self.recorder, session.streaming, context))

    async def _stop_recording(self) -> None:
        await self._restore_volume()
        self.set_state(State.PROCESSING)
        self.sounds.play("stop", blocking=False)
        log.info("Processing...")

        await asyncio.sleep(_POST_ROLL_SECONDS)

        try:
            wav_data = self.recorder.stop()
        except Exception as e:
            log.error("Failed to stop recording: %s", e, exc_info=not isinstance(e, VoxError))
            self._abandon_recording()
            return

        if is_digital_silence(wav_data):
            log.warning(
                "The microphone delivered only silence (every sample zero), which usually means Vox may not use it. "
                "On macOS, allow it in System Settings > Privacy & Security > Microphone."
            )
            self._show_mic_notice(True)
            self._abandon_recording()
            return
        self._show_mic_notice(False)

        # Re-detect window at stop time (user may have switched focus)
        stop_context = await asyncio.to_thread(detect_active_window, self.config)

        session, self.session = self.session, None
        self.inflight = session
        self.process_task = asyncio.create_task(_process(
            wav_data=wav_data,
            config=self.config,
            batch_transcriber=self.batch_transcriber,
            streaming_transcriber=session.streaming,
            stream_task=session.stream_task,
            sounds=self.sounds,
            queue=self.queue,
            context=stop_context,
            screen_capture_future=session.screen_future,
            history=self.history,
            tray=self.tray,
            mode=session.mode,
        ))

    def _abandon_recording(self) -> None:
        """A recording that can't be transcribed: error sound, release what it held, back to idle."""
        self.sounds.play("error")
        self._end_session()
        self.set_state(State.IDLE)

    async def _cancel(self) -> None:
        if self.state is State.RECORDING:
            log.info("Cancelling active recording...")
            await self._restore_volume()
            try:
                self.recorder.discard()
            except Exception:
                log.warning("Error discarding recording", exc_info=True)
            self._end_session()
            self.sounds.play("cancel")
            self.set_state(State.IDLE)
            log.info("Recording cancelled, state reset to IDLE")

        elif self.state is State.PROCESSING:
            log.info("Cancelling in-flight processing...")
            await self._restore_volume()
            if self.process_task is not None and not self.process_task.done():
                self.process_task.cancel()
            # The task may be cancelled before it ever ran, so its own cleanup can't be relied on
            self._end_processing()
            self.sounds.play("cancel")
            self.set_state(State.IDLE)
            log.info("Processing cancelled, state reset to IDLE")

        else:
            log.debug("Ignoring cancel event in IDLE state")

    def _end_session(self) -> None:
        if self.session is not None:
            self.session.release()
            self.session = None

    def _end_processing(self) -> None:
        self.process_task = None
        if self.inflight is not None:
            self.inflight.release()
            self.inflight = None

    def _lower_volume(self, level: float) -> None:
        with self._volume_lock:
            if not self._closing:
                self.saved_volume = _attenuate(level)

    async def _restore_volume(self) -> None:
        volume, self.saved_volume = self.saved_volume, None
        if volume is not None:
            await asyncio.to_thread(set_volume, volume)

    async def _recover(self) -> None:
        """After an unexpected error: restore the volume, release the mic, and get back to a usable state."""
        try:
            await self._restore_volume()
            self.recorder.discard()
            self._end_session()
            # A transcription already under way finishes on its own and reports process_done
            if self.process_task is None or self.process_task.done():
                self._end_processing()
                self.set_state(State.IDLE)
            self.sounds.play("error")
        except Exception:
            log.exception("Could not recover from the error")

    def _shutdown(self, reload_task: asyncio.Task) -> None:
        with self._volume_lock:
            self._closing = True
            if self.saved_volume is not None:
                set_volume(self.saved_volume)  # quitting mid-recording must not leave the volume lowered
                self.saved_volume = None
        if self._device_timer is not None:
            self._device_timer.cancel()
        if self.process_task is not None:
            self.process_task.cancel()
        self._end_session()
        self._end_processing()
        reload_task.cancel()
        self.hotkey.stop()
        self.recorder.close()
        if self.history is not None:
            self.history.close()
        if self.tray is not None:
            self.tray.stop()

    # -- Readiness: API key and transcription mode ---------------------------

    def _key_missing(self) -> bool:
        return self.config.uses_openai and not self.config.openai_api_key

    async def _reload_api_key(self) -> None:
        """Re-read the key off the event loop, since a locked keyring may be waiting on an unlock prompt."""
        config = self.config
        try:
            config.openai_api_key = await asyncio.to_thread(get_api_key)
            config.api_key_error = None
        except KeystoreError as e:
            config.api_key_error = str(e)  # keep any key already in hand
            log.warning("Couldn't read the OpenAI API key from the keychain: %s", e)
        if self.tray is not None:
            self.tray.key_changed()
        self._warm_up_transcriber()

    async def _ready_to_record(self) -> bool:
        config = self.config
        if self._key_missing():
            await self._reload_api_key()  # it may have been saved, or the keyring unlocked, since
        if self._key_missing():
            self.sounds.play("error")
            if config.api_key_error is not None:
                log.warning("Not recording: the keychain couldn't be read (%s)", config.api_key_error)
            else:
                log.warning("Not recording: no OpenAI API key. Choose Set API Key… from the Vox menu.")
                if self.tray is not None:
                    self.tray.open_key_window()
            return False
        return self._ensure_transcriber()

    def _ensure_transcriber(self) -> bool:
        """Have a transcriber for the current mode, which a config edit may have changed.

        While the mode can't run (config.mode_error), each hotkey press tries
        again, so fixing the setup needs no restart.
        """
        config = self.config
        local = config.mode == "whisper_cpp"
        if config.mode_error is None and self.batch_transcriber is not None and self.transcriber_local == local:
            return True
        had_error = config.mode_error is not None
        try:
            transcriber = self._build_transcriber(config.mode)
        except ConfigError as e:
            config.mode_error = str(e)
            log.warning("Not recording: %s", e)
            self.sounds.play("error")
            if self.tray is not None and not had_error:
                self.tray.mode_changed()
            return False
        config.mode_error = None
        self._use_transcriber(transcriber, config.mode)
        if self.tray is not None and had_error:
            self.tray.mode_changed()
        return True

    def _build_transcriber(self, mode: str) -> WhisperTranscriber | WhisperCppTranscriber:
        return WhisperCppTranscriber(self.config) if mode == "whisper_cpp" else WhisperTranscriber(self.config)

    def _use_transcriber(self, transcriber: WhisperTranscriber | WhisperCppTranscriber, mode: str) -> None:
        self.batch_transcriber = transcriber
        self.transcriber_local = mode == "whisper_cpp"
        self._warm_up_transcriber()

    def _warm_up_transcriber(self) -> None:
        """Load the OpenAI SDK and build its client in the background, not after the user stops talking."""
        if self.transcriber_local or self.batch_transcriber is None or not self.config.openai_api_key:
            return
        _spawn(self._warm_up(self.batch_transcriber))

    @staticmethod
    async def _warm_up(transcriber: WhisperTranscriber) -> None:
        try:
            await asyncio.to_thread(transcriber.warm_up)
        except Exception:
            log.warning("Could not prepare the OpenAI client", exc_info=True)

    # -- Menu events -----------------------------------------------------------

    async def _switch_mode(self, mode: str) -> None:
        config = self.config
        if self.state is not State.IDLE or (mode == config.mode and config.mode_error is None):
            return
        try:
            if mode not in MODES:
                raise ConfigError(f"Invalid transcription mode: {mode}")
            if mode != "whisper_cpp" and not config.openai_api_key:
                raise ConfigError("Set an OpenAI API key before selecting OpenAI transcription")
            candidate = self._build_transcriber(mode)
            update_transcription_mode(config.config_path or DEFAULT_CONFIG_PATH, mode)
        except (ConfigError, OSError, ValueError) as e:
            log.warning("Could not switch transcription mode: %s", e)
            self.sounds.play("error")
            return
        config.mode = mode
        config.mode_error = None
        self._use_transcriber(candidate, mode)
        log.info("Transcription mode set to %s", mode)
        if self.tray is not None:
            self.tray.mode_changed()

    def _set_limit(self, value: str) -> None:
        """Persist a recording limit picked from the menu; it applies from the next recording."""
        config = self.config
        try:
            seconds = int(value)
            if seconds <= 0:
                raise ValueError(f"Invalid recording limit: {value}")
            update_max_recording_seconds(config.config_path or DEFAULT_CONFIG_PATH, seconds)
        except (ConfigError, OSError, ValueError) as e:
            log.warning("Could not set the recording limit: %s", e)
            self.sounds.play("error")
            return
        config.max_recording_seconds = seconds
        log.info("Recording limit set to %d s", seconds)
        if self.tray is not None:
            self.tray.limit_changed()

    # -- Notices and input devices --------------------------------------------

    def _show_mic_notice(self, silent: bool) -> None:
        if silent == self.mic_silent:
            return
        self.mic_silent = silent
        if self.tray is not None:
            self.tray.set_notice(SILENT_MIC_NOTICE if silent else _platform_notice())

    def _schedule_device_refresh(self, delay: float) -> None:
        """Send the tray a fresh input-device list soon: PortAudio only sees new devices after a restart."""
        if self.tray is None:
            return
        if self._device_timer is not None:
            self._device_timer.cancel()
        self._device_timer = self.loop.call_later(delay, self._start_device_refresh)

    def _start_device_refresh(self) -> None:
        self._device_timer = None
        self._device_job = asyncio.ensure_future(self._refresh_devices())

    async def _refresh_devices(self) -> None:
        try:
            devices = await asyncio.to_thread(self.recorder.refresh_input_devices)
            if devices is not None and devices != self._devices_sent:
                self._devices_sent = devices
                self.tray.devices_changed(devices)
        except Exception:
            log.exception("Could not refresh the input device list")

    async def _settle_devices(self) -> None:
        """Keep the PortAudio restart clear of new streams and sounds: drop a pending one, finish a running one."""
        if self._device_timer is not None:
            self._device_timer.cancel()
            self._device_timer = None
        job, self._device_job = self._device_job, None
        if job is not None and not job.done():
            await job


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
    """Stream audio chunks live during recording. On failure the session is closed, so finish() fails over to batch."""
    try:
        await streaming_transcriber.connect(context)
        async for chunk in recorder.stream_chunks():
            if streaming_transcriber.closed:
                break
            await streaming_transcriber.send_audio_chunk(chunk)
    except Exception as e:
        if not streaming_transcriber.closed:
            log.warning("Live audio streaming failed: %s (will fall back to OpenAI batch)", e)
            await streaming_transcriber.close()


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
    mode: str | None = None,
) -> None:
    """Transcribe a finished recording (finish the live stream, else batch), paste it, and keep it in history.

    ``mode`` is the mode the recording started in; a config reload may change config.mode meanwhile.
    """
    mode = mode or config.mode
    use_streaming = mode == "streaming" and streaming_transcriber is not None
    try:
        t0 = time.monotonic()

        if not use_streaming:
            if streaming_transcriber is not None:
                if stream_task is not None:
                    stream_task.cancel()
                    await asyncio.wait([stream_task])
                await streaming_transcriber.close()
                streaming_transcriber = stream_task = None
            # VAD gate only for batch modes. Streaming has its own silence/hallucination
            # guard and the local VAD produces false negatives that drop real speech.
            if not await asyncio.to_thread(has_speech, wav_data):
                log.info("No speech detected, skipping transcription")
                if screen_capture_future is not None:
                    screen_capture_future.cancel()
                return

        text: str | None = None
        if use_streaming:
            if screen_capture_future is not None:
                screen_capture_future.cancel()  # a live session got its keywords when it connected
                screen_capture_future = None
            text = await _finish_streaming(streaming_transcriber, stream_task)
            if text is not None:
                log.info("Streaming transcription succeeded in %.3fs", time.monotonic() - t0)
            elif config.context_screen:
                # Falling back to batch: capture the screen words the live session never needed
                screen_capture_future = start_screen_capture(context)

        if text is None:
            if screen_capture_future is not None:
                context.screen_text = await _screen_text(screen_capture_future)
                screen_capture_future = None
            log.info("Using %s transcription", "whisper.cpp" if mode == "whisper_cpp" else "OpenAI batch")
            text = await batch_transcriber.transcribe(wav_data, context)

        if not text or not text.strip():
            log.info("Empty transcription result, skipping injection")
            return

        text = _expand_snippet(text, config)
        pasted = await _paste(text, context, config)
        if not pasted:
            sounds.play("error")  # the text is still in history

        if history is not None:
            saved = await _record_history(history, text, context, wav_data, mode)
            if saved and tray is not None:
                tray.history_changed()

        if pasted:
            log.info("Done in %.3fs (%s)", time.monotonic() - t0, mode)

    except asyncio.CancelledError:
        log.info("Processing task cancelled")
        if screen_capture_future is not None:
            screen_capture_future.cancel()
        if stream_task is not None:
            stream_task.cancel()
        if streaming_transcriber is not None:
            await streaming_transcriber.close()
        raise
    except PartialTranscriptionError as e:
        # The parts that did transcribe are billed: keep them where the user can copy them
        log.error("Transcription failed partway (%s); the first part is saved in history", e)
        sounds.play("error")
        if history is not None and await _record_history(history, e.text, context, wav_data, mode) and tray is not None:
            tray.history_changed()
    except Exception as e:
        log.error("Processing error: %s", e, exc_info=not isinstance(e, VoxError))
        sounds.play("error")
    finally:
        queue.put_nowait("process_done")


async def _finish_streaming(transcriber: StreamingTranscriber, stream_task: asyncio.Task | None) -> str | None:
    """The live transcript, or None when the session failed and batch should transcribe instead.

    No deadline: the worker ends once the recorder's stream does, and finish()
    waits for the server's completion or a connection failure.
    """
    try:
        if stream_task is not None:
            await asyncio.wait([stream_task])
        return await transcriber.finish()
    except Exception as e:
        log.warning(
            "Streaming transcription failed (%s: %s), falling back to OpenAI batch", type(e).__name__, e,
            exc_info=not isinstance(e, StreamingError),
        )
        await transcriber.close()
        return None


async def _screen_text(future: asyncio.Future) -> str:
    try:
        text = await asyncio.wait_for(future, timeout=_SCREEN_WAIT_SECONDS)
    except Exception as e:  # the capture is optional: any failure just means no screen words
        log.debug("Screen capture not ready or failed: %s", e)
        return ""
    if text:
        log.debug("Screen context: %d chars", len(text))
    return text


def _expand_snippet(text: str, config: Config) -> str:
    key = snippet_key(text)
    expansion = next((value for trigger, value in config.snippets.items() if snippet_key(trigger) == key), None)
    if expansion is None:
        return text
    log.info("Expanded a snippet (%d chars)", len(expansion))
    log.debug("Snippet match: %r -> %r", text, expansion)
    return expansion


async def _paste(text: str, context: AppContext, config: Config) -> bool:
    """Paste off the event loop. On Linux the paste chord depends on the app, and focus may
    have moved while transcribing, so the window is looked up again first."""
    app_type = context.app_type
    try:
        if sys.platform != "darwin":
            focused = await asyncio.to_thread(detect_active_window, config)
            if focused.wm_class:  # nothing detected: keep the stop-time guess
                app_type = focused.app_type
        await asyncio.to_thread(paste, text, app_type)
    except Exception as e:
        log.error("Paste failed: %s", e, exc_info=not isinstance(e, InjectionError))
        return False
    return True


async def _record_history(
    history: HistoryDB,
    text: str,
    context: AppContext,
    wav_data: bytes,
    transcription_mode: str,
) -> bool:
    """Persist a dictation. Failures are logged, never raised: the paste already happened."""
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
    except (EOFError, wave.Error, ZeroDivisionError):
        return None


async def _config_reloader(config: Config, sounds: SoundPlayer, recorder: Recorder) -> None:
    """Poll config file mtime and reload hot-reloadable settings every couple of seconds.

    Hotkey settings are read once at startup and need a restart.
    """
    if config.config_path is None:
        return

    last_mtime: float = 0
    try:
        last_mtime = config.config_path.stat().st_mtime
    except OSError:
        pass
    file_audio = (config.audio_device, config.sample_rate, config.channels)

    while True:
        await asyncio.sleep(_CONFIG_POLL_SECONDS)
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
            config.context_screen = new_config.context_screen
            config.sounds_enabled = new_config.sounds_enabled
            config.attenuation_enabled = new_config.attenuation_enabled
            config.attenuation_level = new_config.attenuation_level
            config.max_recording_seconds = new_config.max_recording_seconds  # from the next recording
            config.mode = new_config.mode
            config.streaming_model = new_config.streaming_model
            config.whisper_model = new_config.whisper_model
            config.whisper_cpp_binary = new_config.whisper_cpp_binary
            config.whisper_cpp_model = new_config.whisper_cpp_model
            config.whisper_language = new_config.whisper_language
            config.whisper_prompt = new_config.whisper_prompt

            # Apply audio settings only when the file changed them, so a device picked
            # from the menu bar survives unrelated edits such as vocabulary changes.
            new_file_audio = (new_config.audio_device, new_config.sample_rate, new_config.channels)
            if new_file_audio != file_audio:
                file_audio = new_file_audio
                config.audio_device, config.sample_rate, config.channels = new_file_audio
                recorder.reconfigure(config)  # waits for a recording in progress to end

            sounds._enabled = new_config.sounds_enabled

            log.info("Config reloaded from %s", config.config_path)
        except ConfigError as e:
            log.warning("Config reload failed: %s", e)
        except Exception:
            log.exception("Config reload failed")
