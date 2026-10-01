"""Optional recording feedback. Native objects are touched only by the GUI dispatcher.

The audio callback publishes one latest value; this controller never queues audio or frames.
Lifecycle changes coalesce into one dispatch, and capture workers wait only for a bounded hide.
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

log = logging.getLogger(__name__)
FADE_SECONDS = 0.19
CAPTURE_TIMEOUT = 0.3
PANEL_SIZE = (224, 44)
PANEL_RADIUS = 8  # match the personal-site overlay demo
SIGNAL_X, SIGNAL_WIDTH, SIGNAL_HEIGHT = 50, 153, 20  # 40 rendered pixels beside mic body and inside rim at 2×
CANCEL_RECT = (204, 2, 18, 18)  # top-left origin; only this corner accepts clicks
SIGNAL_Y = PANEL_SIZE[1] / 2  # centered vertically; status sits below


@dataclass(frozen=True)
class Snapshot:
    generation: int = 0
    revision: int = 0
    phase: str = 'hidden'
    mode: str = 'batch'
    context: object = None

    @property
    def status(self):
        if self.phase == 'listening':
            return 'Listening'
        return 'Transcribing locally…' if self.mode == 'whisper_cpp' else 'Transcribing…'


class Envelope:
    def __init__(self):
        self.value = 0.0
        self.time = None

    def sample(self, now, level, generation):
        target = 0.0
        if level is not None and level[0] == generation and 0 <= now - level[1] <= 0.2:
            # Map -60 to -26 dBFS into visible height: ordinary low-level microphone
            # speech barely moved a linear meter. Keep quieter background noise flat.
            if level[2] > 0.001:
                target = min(1.0, (20 * math.log10(level[2]) + 60) / 34)
        dt = 1 / 30 if self.time is None else max(0.0, now - self.time)
        self.time = now
        tau = 0.035 if target > self.value else 0.14
        self.value += (target - self.value) * (1 - math.exp(-dt / tau))
        return self.value


class Overlay:
    def __init__(self, enabled=False, dispatch=None, factory=None, clock=time.monotonic):
        self.enabled = enabled
        self.dispatch = dispatch
        self.factory = factory
        self.clock = clock
        self.snapshot = Snapshot()
        self.backend = None
        self.recorder = None
        self.closed = False
        self.failed = False
        self._pending = False
        self._lock = threading.RLock()
        self._captures = set()
        self._acks = set()
        self._reduced = False
        self._envelope = Envelope()
        self._fade_at = 0.0
        self._processing_at = 0.0
        self._eligible = None
        self.on_cancel = lambda generation: None
        self._cancelled_generation = None

    def request_cancel(self, generation):
        with self._lock:
            if (generation != self.snapshot.generation or self.snapshot.phase not in ("listening", "processing")
                    or self.closed or self.failed or not self.enabled or self._captures):
                return
            self._cancelled_generation = generation
            self.dismiss()
        self.on_cancel(generation)

    def is_cancelled(self, generation):
        # A published scalar: never wait for native drawing/hiding on the daemon loop.
        return self._cancelled_generation == generation

    def _detach(self):
        if self.recorder is not None:
            self.recorder.set_level_generation(None)
            self.recorder = None

    def _change(self, phase, **kwargs):
        old = self.snapshot
        self.snapshot = Snapshot(kwargs.get('generation', old.generation), old.revision + 1,
                                 phase, kwargs.get('mode', old.mode), kwargs.get('context', old.context))
        self._schedule()

    def _schedule(self):
        if self.dispatch is None or self._pending:
            return
        self._pending = True
        try:
            self.dispatch(self._drain)
        except Exception:
            self._pending = False
            self._fail(cleanup=False)  # never touch AppKit from the caller's thread

    def _fail(self, *, cleanup=True):
        with self._lock:
            if not self.failed:
                log.warning('Recording overlay unavailable; dictation continues (restart Vox to retry)')
            self.failed = True
            self._detach()
            old = self.snapshot
            self.snapshot = Snapshot(old.generation, old.revision + 1)
            if cleanup and self.backend is not None:
                backend, self.backend = self.backend, None
                try:
                    backend.close()
                except Exception:
                    pass  # fault already reported; never strand the dictation on teardown
            elif self.backend is not None:
                try:
                    # Independent native queue: the tray dispatcher itself may have broken.
                    self.backend.request_close()
                except Exception:
                    pass  # the next native timer tick also attempts main-thread teardown

    def set_enabled(self, enabled):
        with self._lock:
            self.enabled = enabled
            if not enabled:
                self._eligible = None
                self._detach()
                self._change('hidden')

    def begin(self, generation):
        """Capture the preference before microphone startup; later enabling waits for the next turn."""
        with self._lock:
            self.dismiss()
            self._eligible = generation if self.enabled and not self.closed and not self.failed else None

    def listening(self, generation, context, mode, recorder):
        with self._lock:
            if generation != self._eligible or not self.enabled or self.closed or self.failed or self.factory is None:
                return
            self._detach()
            self.recorder = recorder
            recorder.set_level_generation(generation)
            self._envelope = Envelope()
            self._change('listening', generation=generation, context=context, mode=mode)

    def processing(self, generation):
        with self._lock:
            if self.snapshot.generation == generation and self.snapshot.phase == 'listening':
                self._detach()
                self._processing_at = self.clock()
                self._change('processing')

    def complete(self, generation, success=True):
        with self._lock:
            if self.snapshot.generation != generation or self.snapshot.phase == 'hidden':
                return
            self._detach()
            self._fade_at = self.clock()
            self._change('fading' if success else 'hidden')

    def dismiss(self):
        with self._lock:
            self._eligible = None
            self._detach()
            self._change('hidden')

    def hide_before_paste(self, generation, timeout=CAPTURE_TIMEOUT):
        """Worker-only: dismiss this operation and briefly await the native hide."""
        ack = threading.Event()
        deadline = time.monotonic() + timeout
        if not self._lock.acquire(timeout=timeout):
            return False
        try:
            if self.snapshot.generation != generation:
                return True
            if self.backend is None or self.failed:
                self.complete(generation, False)
                return not self.failed
            self._acks.add(ack)
            self.complete(generation, False)
            self._schedule()  # also acknowledge an already-hidden error/partial result
        finally:
            self._lock.release()
        try:
            return ack.wait(max(0, deadline - time.monotonic()))
        finally:
            # Native hide holds the lock. Never wait on it again after our deadline.
            if self._lock.acquire(blocking=False):
                try:
                    self._acks.discard(ack)
                finally:
                    self._lock.release()

    def close(self):
        with self._lock:
            self.closed = True
            self._eligible = None
            self._detach()
            self._change('hidden')

    def _drain(self):
        with self._lock:
            self._pending = False
            try:
                hidden = self.snapshot.phase == 'hidden' or bool(self._captures)
                if self.closed or not self.enabled or self.failed:
                    if self.backend is not None:
                        backend, self.backend = self.backend, None
                        backend.close()
                elif hidden:
                    if self.backend is not None:
                        self.backend.timer(None)
                        self.backend.hide()
                else:
                    if self.backend is None:
                        self.backend = self.factory()
                        self._reduced = self.backend.reduced_motion
                        self.backend.observe_motion(self._motion_changed)
                        self.backend.on_error = self._fail
                        self.backend.on_cancel = self.request_cancel
                    self.backend.show(self.snapshot)
                    self._tick(self.snapshot)
                    if self.snapshot.phase != 'hidden':
                        snapshot = self.snapshot
                        self.backend.timer(None if self._reduced else lambda: self._tick(snapshot))
                # Acknowledgment follows the actual native hide/close, never just its dispatch.
                if not self.failed:
                    for ack in self._acks:
                        ack.set()
                self._acks.clear()
            except Exception:
                self._acks.clear()
                self._fail()

    def _motion_changed(self, reduced):
        with self._lock:
            self._reduced = reduced
            self._change(self.snapshot.phase)

    def _tick(self, snapshot):
        with self._lock:
            if self.failed:
                self._fail()
                return
            if snapshot != self.snapshot or self._captures or self.closed or self.backend is None:
                return
            try:
                now = self.clock()
                opacity = 1.0
                if snapshot.phase == 'fading':
                    opacity = 0.0 if self._reduced else max(0.0, 1 - (now - self._fade_at) / FADE_SECONDS)
                    if not opacity:
                        self.backend.timer(None)
                        self.backend.hide()
                        self.snapshot = Snapshot(snapshot.generation, snapshot.revision + 1)
                        return
                level = self.recorder.latest_level if self.recorder is not None else None
                amplitude = self._envelope.sample(now, level, snapshot.generation) if snapshot.phase == 'listening' else 0.0
                # The carrier drifts decoratively; its height comes only from microphone levels.
                phase = now if snapshot.phase == 'listening' else now - self._processing_at
                self.backend.draw(0.0 if self._reduced else amplitude, 0.0 if self._reduced else phase, opacity)
            except Exception:
                self._fail()

    def defer_for_capture(self):
        """Reserve suppression before queuing screen capture; return an idempotent release.

        The worker may wait for the OCR pool or try accessibility before taking a
        screenshot. Reserving on the daemon loop prevents a show/hide/show flash.
        """
        token = object()
        with self._lock:
            self._captures.add(token)

        def release():
            with self._lock:
                if token in self._captures:
                    self._captures.remove(token)
                    self._schedule()

        return release

    @contextmanager
    def capture_guard(self, timeout=CAPTURE_TIMEOUT):
        """Worker-only guard for screenshot acquisition (OCR itself need not hold it).

        Suppression is shared across generations so a new recording cannot appear in an
        older in-progress screenshot. Releasing a lease only renders the current state.
        """
        token, ack = object(), threading.Event()
        with self._lock:
            if self.factory is None:
                ack.set()
            else:
                self._captures.add(token)
                self._acks.add(ack)
                if self.backend is None and not self.failed:
                    # No native panel has ever appeared (including explicitly disabled operation).
                    # The lease still suppresses a recording enabled during this capture.
                    ack.set()
                else:
                    self._schedule()
        try:
            yield ack.wait(timeout)
        finally:
            with self._lock:
                self._captures.discard(token)
                self._acks.discard(ack)
                if self.factory is not None:
                    self._schedule()


def create_overlay(enabled, dispatch=None):
    if dispatch is None:
        return Overlay(enabled)

    if sys.platform.startswith('linux'):
        if not os.environ.get('DISPLAY'):
            return Overlay(enabled)

        def factory():
            from .x11.overlay import NativeOverlay
            return NativeOverlay()

        return Overlay(enabled, dispatch, factory)

    if sys.platform != 'darwin':
        return Overlay(enabled)

    def factory():
        from .mac.overlay import NativeOverlay
        return NativeOverlay()

    return Overlay(enabled, dispatch, factory)


# All coordinates here are logical points. Quartz uses a top-left origin; AppKit uses
# a bottom-left origin relative to the primary display, even for mixed backing scales.
def quartz_to_appkit(rect, primary_height):
    x, y, width, height = rect
    return x, primary_height - y - height, width, height


def panel_frame(screens, target=None, *, top_left=False):
    """Screens are (id, full frame, visible frame), primary first. Returns id and frame."""
    def intersection(screen):
        x, y, w, h = screen[1]
        tx, ty, tw, th = target
        return max(0, min(x + w, tx + tw) - max(x, tx)) * max(0, min(y + h, ty + th) - max(y, ty))

    screen = max(screens, key=intersection) if target else screens[0]
    x, y, w, h = screen[2]
    width, height = min(PANEL_SIZE[0], w), min(PANEL_SIZE[1], h)
    inset = min(24, max(0, h - height))
    bottom = y + h - height - inset if top_left else y + inset
    return screen[0], (x + (w - width) / 2, bottom, width, height)


def wave_paths(amplitude, phase, width=142, height=42):
    """Reference-style carrier geometry shared by the native renderers, back to front."""
    for layer in (2, 1, 0):
        points = []
        for x in range(0, width + 2, 2):
            x = min(x, width)  # include the exact endpoint for odd signal widths too
            u = x / width
            envelope = math.sin(u * math.pi) ** 1.8
            syllable = 0.5 + 0.5 * math.sin(u * 19 - phase * 4.5)
            carrier = math.sin(u * (70 + layer * 9) - phase * (5.5 + layer)) * 0.7
            carrier += math.sin(u * 33 + phase * 3.5 + layer) * 0.3
            points.append((x, carrier * envelope * syllable * amplitude * height * (0.43 - layer * 0.06)))
        yield layer, points


def bead_circles(phase, width=142):
    """Loop a glowing bead left-to-right every two seconds; phase zero is a static line.

    Fade at the endpoints to avoid a bright jump when wrapping. This is activity,
    never a percentage. Shared logical geometry keeps the native renderers aligned.
    """
    progress = (phase / 2.0) % 1.0
    strength = min(1.0, progress / 0.08, (1 - progress) / 0.08)
    center = 8 + (width - 16) * progress
    for radius, alpha in ((8, 0.035), (6.5, 0.055), (5, 0.1), (3.5, 0.25), (2.2, 1), (1.1, 0.9)):
        yield center, radius, alpha * strength
