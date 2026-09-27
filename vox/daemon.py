"""Asyncio event loop and state machine orchestrator."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import signal
import sys
import threading
import time
import wave
from collections.abc import Coroutine
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
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
from .modes import mode_problem
from .sounds import SoundPlayer, sound_playing_until
from .streaming import StreamingTranscriber
from .transcribe import PartialTranscriptionError, Transcriber
from .whisper_cpp import WhisperCppTranscriber, uses_screen_hints
from .window import AppContext, detect_active_window, start_screen_capture

if TYPE_CHECKING:
    from .ui.tray import TrayManager

log = logging.getLogger(__name__)

WAYLAND_NOTICE = "Wayland: hotkey and paste only work in X11 apps"
SILENT_MIC_NOTICE = "Microphone is silent: check its permission"
ACCESSIBILITY_NOTICE = "Accessibility access needed"
PARTIAL_NOTICE = "Last dictation only partly transcribed: see History"

# Trailing audio kept after the stop key, so the last syllable isn't cut off
_POST_ROLL_SECONDS = 0.12
# How long a batch transcription waits for the screen capture started with the recording
_SCREEN_WAIT_SECONDS = 1.5
# The device re-scan restarts PortAudio, which on Linux also plays Vox's sounds: let them finish first.
# It waits this long after returning to idle, or longer while a sound is still playing (a custom one).
_DEVICE_REFRESH_DELAY = 1.0
# macOS also re-scans while idle, since the restart costs about 1 ms there. On Linux it costs about 45 ms,
# and PipeWire's or PulseAudio's "default" device already follows hotplugs, so the re-scan after a recording does.
_IDLE_DEVICE_SCAN_SECONDS: float | None = 30.0 if sys.platform == "darwin" else None
_CONFIG_POLL_SECONDS = 2.0


class State(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    PROCESSING = "PROCESSING"


class Outcome(Enum):
    """How the transcription of a finished recording went, when it produced text."""

    TRANSCRIBED = "transcribed"
    PARTIAL = "partial"  # a recording sent in parts failed partway; the parts before are in history


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
    # With a config error the mode in the file is unknown, and it may not need a key
    if tray is None and config.uses_openai and not config.openai_api_key and config.config_error is None:
        # Nowhere to ask for a key; headless Vox is started by hand, not by a service that would retry
        reason = f" ({config.api_key_error})" if config.api_key_error else ""
        log.error(
            "No OpenAI API key%s. Start Vox Transfer from the desktop and choose Set API Key… from its menu, "
            "or set OPENAI_API_KEY.", reason,
        )
        raise SystemExit(1)

    notice = _platform_notice()
    if notice == WAYLAND_NOTICE:
        log.warning(
            "This is a Wayland session. Vox Transfer's hotkey, window detection and paste use X11, so they only "
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
    _quit_tray_on_sigterm(tray)
    thread.start()
    try:
        tray.run()
    finally:
        # The GUI loop that handled SIGTERM has ended: from here on it ends Vox at once
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    tray.request_quit()  # no-op if the daemon already exited
    thread.join(timeout=5)
    if errors:
        raise errors[0]


def _quit_tray_on_sigterm(tray: TrayManager) -> None:
    """Make SIGTERM (a service stop, a logout, an upgrade) quit the way Quit does, so a lowered volume is restored.

    The tray's GUI loop owns the main thread, so the handler runs in it: through a GLib signal source
    on Linux, and a Mach port on macOS (as pystray does for SIGINT). A second SIGTERM ends Vox at once.
    """

    def on_sigterm(*_: object) -> bool:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        log.info("Quitting on SIGTERM")
        tray.request_quit()
        return False  # GLib: remove the source

    try:
        if sys.platform == "darwin":
            from PyObjCTools import MachSignals

            MachSignals.signal(signal.SIGTERM, on_sigterm)
        else:
            from gi.repository import GLib

            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, on_sigterm)
    except Exception:
        log.warning("Couldn't handle SIGTERM: stopping Vox Transfer that way won't restore a lowered volume", exc_info=True)


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
    daemon = _Daemon(config, tray)
    if tray is not None:  # run() has the tray's GUI loop handle SIGTERM
        await daemon.run()
        return

    # Headless, asyncio.run owns the main thread: SIGTERM quits the way Ctrl-C does, so a lowered volume is restored
    loop, task = asyncio.get_running_loop(), asyncio.current_task()

    def on_sigterm() -> None:
        loop.remove_signal_handler(signal.SIGTERM)  # a second SIGTERM ends Vox at once
        log.info("Quitting on SIGTERM")
        task.cancel()

    loop.add_signal_handler(signal.SIGTERM, on_sigterm)
    try:
        await daemon.run()
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


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
        # What the tray's notice says, most pressing first; the platform's notice shows when neither is set
        self.mic_silent = False
        self.partly_transcribed = False

        self.batch_transcriber: Transcriber | WhisperCppTranscriber | None = None
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
        log.info("Vox Transfer ready (%s mode). Press %s to toggle recording.", config.mode, config.hotkey)
        if tray is not None:
            tray.attach(self.loop, self.queue, self.history, asyncio.current_task())
            if self._key_missing() and config.api_key_error is None and config.config_error is None:
                tray.open_key_window()
        reload_task = asyncio.create_task(_config_reloader(config, self.recorder, tray))
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
                self._scan_devices_while_idle()
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
            task = self.process_task
            if self.state is State.PROCESSING and (task is None or task.done()):
                outcome = task.result() if task is not None and not task.cancelled() else None
                if outcome is not None:
                    self._update_notice(partly_transcribed=outcome is Outcome.PARTIAL)
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
            self._schedule_device_refresh(max(_DEVICE_REFRESH_DELAY, sound_playing_until() - time.monotonic() + 0.1))

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
        self.sounds.play("start")

        # 3. Attenuate volume
        if config.attenuation_enabled:
            await asyncio.to_thread(self._lower_volume, config.attenuation_level)

        # 4. Window context, and the screen capture that runs while the user speaks. Streaming sends
        #    its keywords when it connects, before any capture could finish, so it doesn't start one.
        #    Local mode on Linux leaves screen words out of its prompt, so it doesn't capture either.
        context = await asyncio.to_thread(detect_active_window, config)
        local_without_hints = session.mode == "whisper_cpp" and not uses_screen_hints()
        if config.context_screen and session.mode != "streaming" and not local_without_hints:
            session.screen_future = start_screen_capture(context)

        # 5. If streaming mode, initiate streaming connection and chunk worker
        if session.mode == "streaming":
            session.streaming = StreamingTranscriber(config)
            session.stream_task = asyncio.create_task(_stream_worker(self.recorder, session.streaming, context))
            session.stream_task.add_done_callback(lambda _: self._stream_ended(session))

    def _stream_ended(self, session: _Session) -> None:
        """A live session is done. If it gave up early, the recorder must stop queueing audio for it."""
        if session is self.session or session is self.inflight:  # not one released for a newer recording
            self.recorder.stop_streaming()

    async def _stop_recording(self) -> None:
        await self._restore_volume()
        self.set_state(State.PROCESSING)
        self.sounds.play("stop")
        log.info("Processing...")

        await asyncio.sleep(_POST_ROLL_SECONDS)

        try:
            wav_data = self.recorder.stop()
        except Exception as e:
            log.error("Failed to stop recording: %s", e, exc_info=not isinstance(e, VoxError))
            self._abandon_recording()
            return

        if await asyncio.to_thread(is_digital_silence, wav_data):  # reads every sample: off the loop
            log.warning(
                "The microphone delivered only silence (every sample zero), which usually means Vox Transfer may not use it. "
                "On macOS, allow it in System Settings > Privacy & Security > Microphone."
            )
            self._update_notice(mic_silent=True)
            self._abandon_recording()
            return
        self._update_notice(mic_silent=False)

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
            self.sounds.play("error")  # before set_state, so the IDLE-return rescan waits for it
            # A transcription already under way finishes on its own and reports process_done
            if self.process_task is None or self.process_task.done():
                self._end_processing()
                self.set_state(State.IDLE)
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
        if config.config_error is not None:
            # Running on defaults: the file may choose local-only transcription, so no audio may go to OpenAI
            self.sounds.play("error")
            log.warning("Not recording until the settings file loads: %s", config.config_error)
            return False
        if self._key_missing():
            await self._reload_api_key()  # it may have been saved, or the keyring unlocked, since
        if self._key_missing():
            self.sounds.play("error")
            if config.api_key_error is not None:
                log.warning("Not recording: the keychain couldn't be read (%s)", config.api_key_error)
            else:
                log.warning("Not recording: no OpenAI API key. Choose Set API Key… from the Vox Transfer menu.")
                if self.tray is not None:
                    self.tray.open_key_window()
            return False
        return self._ensure_transcriber()

    def _ensure_transcriber(self) -> bool:
        """Have a transcriber for the current mode, which a config edit may have changed.

        A whisper.cpp setup is checked on every press (a PATH lookup and a stat), since its binary or
        model can go away while Vox runs. While the mode can't run (config.mode_error), each hotkey
        press tries again, so fixing the setup needs no restart.
        """
        config = self.config
        local = config.mode == "whisper_cpp"
        if not local and config.mode_error is None and self.batch_transcriber is not None and not self.transcriber_local:
            return True
        old_error = config.mode_error
        try:
            transcriber = self._build_transcriber(config.mode)
        except ConfigError as e:
            config.mode_error = str(e)
            log.warning("Not recording: %s", e)
            self.sounds.play("error")
            if self.tray is not None and config.mode_error != old_error:
                self.tray.mode_changed()
            return False
        config.mode_error = None
        self._use_transcriber(transcriber, config.mode)
        if self.tray is not None and old_error is not None:
            self.tray.mode_changed()
        return True

    def _build_transcriber(self, mode: str) -> Transcriber | WhisperCppTranscriber:
        """The transcriber for ``mode``. Raises ConfigError when a whisper.cpp setup can't run."""
        return WhisperCppTranscriber(self.config) if mode == "whisper_cpp" else Transcriber(self.config)

    def _use_transcriber(self, transcriber: Transcriber | WhisperCppTranscriber, mode: str) -> None:
        self.batch_transcriber = transcriber
        self.transcriber_local = mode == "whisper_cpp"
        self._warm_up_transcriber()

    def _warm_up_transcriber(self) -> None:
        """Load the OpenAI SDK and build its client in the background, not after the user stops talking."""
        if self.transcriber_local or self.batch_transcriber is None or not self.config.openai_api_key:
            return
        _spawn(self._warm_up(self.batch_transcriber))

    @staticmethod
    async def _warm_up(transcriber: Transcriber) -> None:
        try:
            await asyncio.to_thread(transcriber.warm_up)
        except Exception:
            log.warning("Could not prepare the OpenAI client", exc_info=True)

    # -- Menu events -----------------------------------------------------------

    def _settings_file_broken(self, change: str) -> bool:
        """Refuse a menu change while config.toml doesn't load: Vox can't know what the file sets."""
        if self.config.config_error is None:
            return False
        log.warning("Can't change the %s until the settings file loads: %s", change, self.config.config_error)
        self.sounds.play("error")
        return True

    async def _switch_mode(self, mode: str) -> None:
        config = self.config
        if self.state is not State.IDLE or self._settings_file_broken("transcription mode"):
            return
        if mode == config.mode and config.mode_error is None:
            return
        try:
            # Building the transcriber tries the whisper.cpp setup, so mode_problem needn't
            problem = mode_problem(config, mode, check_setup=False)
            if problem is not None:
                raise ConfigError(problem)
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
        if self._settings_file_broken("recording limit"):
            return
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

    def _update_notice(self, *, mic_silent: bool | None = None, partly_transcribed: bool | None = None) -> None:
        """Note what the last recording showed, and give the tray the most pressing notice if that changed it."""
        state = (
            self.mic_silent if mic_silent is None else mic_silent,
            self.partly_transcribed if partly_transcribed is None else partly_transcribed,
        )
        if state == (self.mic_silent, self.partly_transcribed):
            return
        self.mic_silent, self.partly_transcribed = state
        if self.tray is not None:
            notice = SILENT_MIC_NOTICE if self.mic_silent else PARTIAL_NOTICE if self.partly_transcribed else None
            self.tray.set_notice(notice or _platform_notice())

    def _scan_devices_while_idle(self) -> None:
        """Where a re-scan is cheap, keep one scheduled while idle, so a new microphone shows up without a recording."""
        if _IDLE_DEVICE_SCAN_SECONDS is not None and self.state is State.IDLE and self._device_timer is None:
            self._schedule_device_refresh(_IDLE_DEVICE_SCAN_SECONDS)

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
        self._scan_devices_while_idle()

    async def _settle_devices(self) -> None:
        """Keep the PortAudio restart clear of new streams and sounds: finish a running one, drop a pending one."""
        job, self._device_job = self._device_job, None
        if job is not None and not job.done():
            await job  # at most one re-scan: about 1 ms on macOS, 45 ms on Linux
        if self._device_timer is not None:  # including the idle re-scan the job just scheduled
            self._device_timer.cancel()
            self._device_timer = None


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
    batch_transcriber: Transcriber | WhisperCppTranscriber,
    streaming_transcriber: StreamingTranscriber | None,
    stream_task: asyncio.Task | None,
    sounds: SoundPlayer,
    queue: asyncio.Queue[str],
    context: AppContext,
    screen_capture_future: asyncio.Future | None,
    mode: str,
    history: HistoryDB | None = None,
    tray: TrayManager | None = None,
) -> Outcome | None:
    """Transcribe a finished recording (finish the live stream, else batch), paste it, and keep it in history.

    ``mode`` is the mode the recording started in; a config reload may change config.mode meanwhile.
    Returns how the transcription went, or None when it failed or gave no text.
    """
    use_streaming = streaming_transcriber is not None  # only a streaming recording has one
    provider = mode  # what history records: a live session that failed hands the recording to batch
    try:
        t0 = time.monotonic()

        if not use_streaming:
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
            if use_streaming:
                provider = "batch"
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
            saved = await _record_history(history, text, context, wav_data, provider)
            if saved and tray is not None:
                tray.history_changed()

        if pasted:
            log.info("Done in %.3fs (%s)", time.monotonic() - t0, provider)
        return Outcome.TRANSCRIBED

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
        sounds.play("error")
        if history is not None and await _record_history(history, e.text, context, wav_data, provider):
            log.error("Transcription failed partway (%s); the parts transcribed so far are saved in history", e)
            if tray is not None:
                tray.history_changed()
            return Outcome.PARTIAL  # the tray points to History until a dictation succeeds
        log.error(
            "Transcription failed partway (%s) and history is unavailable, so the parts transcribed so far "
            "are pasted", e,
        )
        await _paste(e.text, context, config)
    except Exception as e:
        log.error("Processing error: %s", e, exc_info=not isinstance(e, VoxError))
        sounds.play("error")
    finally:
        queue.put_nowait("process_done")
    return None


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


def _file_stamp(path: Path) -> tuple[int, int, int] | None:
    """Changes whenever the file does, also when an older copy is moved back over it; None if it is missing."""
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size, st.st_ino


async def _config_reloader(config: Config, recorder: Recorder, tray: TrayManager | None = None) -> None:
    """Poll config.toml every couple of seconds and apply the settings that can change while Vox runs.

    Hotkey settings are read once at startup and need a restart. While config.config_error is set,
    the file is tried on every poll, and the first version that loads (or its deletion, which means
    the defaults) is applied and clears it, so Vox records again.
    """
    path = config.config_path
    if path is None:
        return

    last_stamp = _file_stamp(path)
    file_audio = (config.audio_device, config.sample_rate, config.channels)
    last_failure = config.config_error  # __main__ has logged that one

    while True:
        await asyncio.sleep(_CONFIG_POLL_SECONDS)
        stamp = _file_stamp(path)
        changed, last_stamp = stamp != last_stamp, stamp
        # A fix need not change the stamp (chmod), may predate the first stamp, or may be deleting
        # the file, so a file that doesn't load is retried whatever the stamp says.
        if config.config_error is None and (stamp is None or not changed):
            continue
        try:
            new_config = load_config(path)

            config.snippets = new_config.snippets
            config.dictionary = new_config.dictionary
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
            # The status line names a problem with the mode the file now selects, not one it moved away
            # from; without a key an OpenAI mode is reported apart from this
            mode_error = mode_problem(config, "whisper_cpp") if config.mode == "whisper_cpp" else None
            if mode_error is not None and mode_error != config.mode_error:
                log.warning("Local transcription can't run: %s", mode_error)
            config.mode_error = mode_error

            # Apply audio settings only when the file changed them, so a device picked
            # from the menu bar survives unrelated edits such as vocabulary changes.
            new_file_audio = (new_config.audio_device, new_config.sample_rate, new_config.channels)
            if new_file_audio != file_audio:
                file_audio = new_file_audio
                config.audio_device, config.sample_rate, config.channels = new_file_audio
                recorder.reconfigure(config)  # waits for a recording in progress to end

            if config.config_error is not None:
                config.config_error = None
                if stamp is None:
                    log.info("%s is gone: Vox Transfer is using the default settings and records again", path)
                else:
                    log.info("%s loads again: Vox Transfer is using its settings and records again", path)
                hotkey = (new_config.hotkey, new_config.hotkey_fallback, new_config.double_tap_timeout_ms)
                if hotkey != (config.hotkey, config.hotkey_fallback, config.double_tap_timeout_ms):
                    log.warning("The hotkey settings in %s take effect when Vox Transfer restarts", path)
            else:
                log.info("Config reloaded from %s", path)
            if tray is not None:
                tray.mode_changed()  # the menu shows the mode and the limit, and the status line the problems
        except ConfigError as e:
            if changed or str(e) != last_failure:  # a retry that fails the same way stays quiet
                log.warning("Config reload failed: %s", e)
            last_failure = str(e)
            if config.config_error is not None:
                config.config_error = str(e)  # refused toggles name the problem the file has now
        except Exception as e:
            if changed or repr(e) != last_failure:
                log.exception("Config reload failed")
            last_failure = repr(e)
