"""Race, motion, capture and failure contracts without a GUI or a microphone."""
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from vox.ui.overlay import Envelope, Overlay, panel_frame, quartz_to_appkit


class Backend:
    reduced_motion = False

    def __init__(self):
        self.visible = False
        self.callback = None
        self.frames = []
        self.snapshot = None
        self.closed = False

    def show(self, snapshot):
        self.snapshot = snapshot
        self.visible = True

    def draw(self, *frame):
        self.frames.append(frame)

    def hide(self):
        self.visible = False

    def timer(self, callback):
        self.callback = callback

    def observe_motion(self, callback):
        self.motion = callback

    def close(self):
        self.closed = True
        self.hide()
        self.timer(None)
        self.motion = None


@pytest.fixture
def rig():
    queue, made, now = [], [], [1.0]

    def factory():
        backend = Backend()
        made.append(backend)
        return backend

    overlay = Overlay(True, queue.append, factory, lambda: now[0])
    recorder = SimpleNamespace(latest_level=None, set_level_generation=Mock())

    def flush():
        while queue:
            queue.pop(0)()

    def start(generation=1):
        overlay.begin(generation)
        overlay.listening(generation, None, 'batch', recorder)

    return SimpleNamespace(overlay=overlay, queue=queue, made=made, now=now, recorder=recorder, flush=flush, start=start)


def test_slow_dispatch_coalesces_and_reads_latest_state(rig):
    rig.start()
    for _ in range(100):
        rig.overlay.processing(1)
    assert len(rig.queue) == 1
    rig.flush()
    assert len(rig.made) == 1
    assert rig.made[0].snapshot.status == 'Transcribing…'
    rig.recorder.set_level_generation.assert_called_with(None)


def test_old_fade_and_completion_cannot_touch_restart(rig):
    rig.start()
    rig.flush()
    backend = rig.made[0]
    rig.overlay.complete(1)
    rig.flush()
    old_tick = backend.callback
    rig.start(2)
    rig.flush()
    rig.now[0] += 2
    old_tick()
    rig.overlay.complete(1, False)
    assert backend.visible and backend.snapshot.generation == 2
    assert rig.overlay.snapshot.phase == 'listening'


def test_cancel_before_dispatch_and_after_show_then_restart(rig):
    rig.start()
    rig.overlay.dismiss()
    rig.flush()
    assert not rig.made
    rig.start(2)
    rig.flush()
    backend = rig.made[0]
    late_tick = backend.callback
    rig.overlay.dismiss()
    rig.flush()
    count = len(backend.frames)
    late_tick()
    assert not backend.visible and backend.callback is None
    assert len(backend.frames) == count
    rig.start(3)
    rig.flush()
    assert backend.visible


def test_paste_hide_timeout_is_bounded_and_does_not_hide_new_generation(rig):
    rig.start()
    rig.flush()
    assert not rig.overlay.hide_before_paste(1, timeout=0)
    assert rig.overlay.snapshot.phase == 'hidden'
    assert not rig.overlay._acks
    rig.start(2)
    rig.flush()
    assert rig.overlay.hide_before_paste(1, timeout=0)
    assert rig.made[0].visible and rig.overlay.snapshot.generation == 2


@pytest.mark.parametrize('already_hiding', [False, True])
def test_paste_hide_deadline_survives_stalled_native_hide(rig, already_hiding):
    rig.start()
    rig.flush()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    result = []

    def stalled_hide():
        entered.set()
        release.wait(2)

    def worker():
        result.append(rig.overlay.hide_before_paste(1, timeout=0.05))
        finished.set()

    rig.made[0].hide = stalled_hide
    if already_hiding:
        rig.overlay.complete(1, False)
        ui = threading.Thread(target=rig.flush)
        ui.start()
        assert entered.wait(1)
        waiter = threading.Thread(target=worker)
        waiter.start()
    else:
        queued = threading.Event()
        rig.overlay.dispatch = lambda callback: (rig.queue.append(callback), queued.set())
        waiter = threading.Thread(target=worker)
        waiter.start()
        assert queued.wait(1)
        ui = threading.Thread(target=rig.flush)
        ui.start()
        assert entered.wait(1)
    try:
        assert finished.wait(0.5), 'paste must not wait for the stalled native hide'
        assert result == [False]
    finally:
        release.set()
        waiter.join(1)
        ui.join(1)
    assert not rig.overlay._acks


def test_enable_waits_for_next_recording_even_during_startup(rig):
    overlay = rig.overlay
    overlay.set_enabled(False)
    overlay.begin(1)
    overlay.set_enabled(True)
    overlay.listening(1, None, 'batch', rig.recorder)
    rig.flush()
    assert not rig.made
    rig.start(2)
    overlay.set_enabled(False)
    overlay.set_enabled(True)
    overlay.listening(2, None, 'batch', rig.recorder)
    rig.flush()
    assert not rig.made
    rig.start(3)
    rig.flush()
    assert rig.made[0].visible
    overlay.set_enabled(False)
    rig.flush()
    assert rig.made[0].closed
    rig.recorder.set_level_generation.assert_called_with(None)


def test_smoothing_attack_release_staleness_and_identity():
    envelope = Envelope()
    first = envelope.sample(1, (1, 1, 0.1), 1)
    attack = envelope.sample(1.06, (1, 1.06, 0.1), 1)
    release = envelope.sample(1.12, (1, 1.12, 0), 1)
    assert 0 < first < attack < 1
    # Compare the fraction of the remaining distance, not raw deltas near saturation.
    assert 0 < (attack - release) / attack < (attack - first) / (1 - first) < 1
    assert envelope.sample(2.4, (1, 1.06, 1), 1) < 0.01
    assert envelope.sample(3, (2, 3, 1), 1) < 0.001


def test_meter_staleness_tolerates_jitter_and_clock_race():
    envelope = Envelope()
    # Cross-thread race where audio callback updates timestamp slightly ahead of GUI sample
    assert envelope.sample(1.0, (1, 1.02, 0.05), 1) > 0
    # Jitter of 250ms (common CoreAudio buffer / scheduling delay) does not flatline
    assert envelope.sample(1.25, (1, 1.00, 0.05), 1) > 0
    # Jitter of 500ms still does not flatline
    assert envelope.sample(1.50, (1, 1.00, 0.05), 1) > 0
    # Stale updates older than LEVEL_TIMEOUT (0.6s) decay to flat
    assert envelope.sample(2.3, (1, 1.00, 0.05), 1) < 0.01


def test_reduced_motion_static_status_no_timer_or_fade(rig):
    rig.start()
    rig.flush()
    backend = rig.made[0]
    old_tick = backend.callback
    backend.motion(True)
    rig.flush()
    assert backend.callback is None
    assert backend.frames[-1][:2] == (0, 0)
    before = len(backend.frames)
    old_tick()
    assert len(backend.frames) == before
    rig.overlay.processing(1)
    rig.flush()
    assert backend.snapshot.status == 'Transcribing…'
    rig.overlay.complete(1)
    rig.flush()
    assert not backend.visible and backend.callback is None
    rig.overlay.close()
    rig.flush()
    assert backend.closed and backend.motion is None


def test_fade_is_short_and_processing_never_implies_completion(rig):
    rig.start()
    rig.overlay.processing(1)
    rig.flush()
    backend = rig.made[0]
    for now in (2, 10, 1000):
        rig.now[0] = now
        backend.callback()
        assert backend.visible and backend.frames[-1][2] == 1
    rig.overlay.complete(1)
    rig.flush()
    rig.now[0] += 0.1
    backend.callback()
    assert 0 < backend.frames[-1][2] < 1
    rig.now[0] += 0.1
    backend.callback()
    assert not backend.visible and backend.callback is None


def test_capture_acknowledges_hide_and_overlapping_leases(rig):
    rig.start()
    rig.flush()
    backend = rig.made[0]
    entered, release, entered2, release2 = [threading.Event() for _ in range(4)]
    results = []

    def capture(ready, done):
        with rig.overlay.capture_guard(timeout=2) as hidden:
            results.append((hidden, backend.visible, backend.callback))
            ready.set()
            assert done.wait(2)

    first = threading.Thread(target=capture, args=(entered, release))
    second = threading.Thread(target=capture, args=(entered2, release2))
    first.start()
    assert not entered.wait(0.02)  # merely enqueuing the hide is insufficient
    rig.flush()
    assert entered.wait(1)
    second.start()
    assert not entered2.wait(0.02)
    rig.flush()
    assert entered2.wait(1)
    release.set()
    first.join(1)
    rig.flush()
    assert not backend.visible
    rig.start(2)
    rig.flush()
    assert not backend.visible  # a newer recording cannot contaminate the older screenshot
    release2.set()
    second.join(1)
    rig.flush()
    assert results == [(True, False, None)] * 2
    assert backend.visible and backend.snapshot.generation == 2


@pytest.mark.parametrize('action', ['processing', 'dismiss', 'restart', 'disable', 'close'])
def test_deferred_capture_release_renders_current_state(rig, action):
    release = rig.overlay.defer_for_capture()
    rig.start()
    rig.flush()
    assert not rig.made
    if action == 'restart':
        rig.start(2)
    elif action == 'disable':
        rig.overlay.set_enabled(False)
    elif action == 'processing':
        rig.overlay.processing(1)
    else:
        getattr(rig.overlay, action)()
    rig.flush()
    release()
    release()
    rig.flush()
    assert not rig.overlay._captures
    if action in ('processing', 'restart'):
        assert rig.made[0].visible
        assert rig.made[0].snapshot == rig.overlay.snapshot
    else:
        assert not rig.made


@pytest.mark.parametrize('action', ['dismiss', 'close', 'disable'])
def test_capture_release_does_not_resurrect_ineligible_panel(rig, action):
    rig.start()
    rig.flush()
    # Inline dispatch models the UI acknowledging each worker request.
    rig.overlay.dispatch = lambda fn: fn()
    with rig.overlay.capture_guard() as hidden:
        assert hidden and not rig.made[0].visible
        if action == 'disable':
            rig.overlay.set_enabled(False)
        else:
            getattr(rig.overlay, action)()
    assert not rig.made[0].visible


def test_capture_timeout_skips_then_late_dispatch_uses_latest_state(rig):
    rig.start()
    rig.flush()
    with rig.overlay.capture_guard(timeout=0) as hidden:
        assert not hidden
    rig.overlay.dismiss()
    rig.flush()
    assert not rig.made[0].visible


@pytest.mark.parametrize('method', ['show', 'draw', 'timer', 'hide', 'close'])
def test_backend_errors_disable_once_detach_and_leave_bounded_work(rig, method, caplog):
    rig.start()
    rig.flush()
    backend = rig.made[0]
    setattr(backend, method, Mock(side_effect=RuntimeError('failed')))
    if method == 'close':
        rig.overlay.close()
    elif method == 'hide':
        rig.overlay.dismiss()
    else:
        rig.overlay.processing(1)
    rig.flush()
    assert rig.overlay.failed
    assert rig.overlay.recorder is None
    for i in range(10):
        rig.start(i + 2)
    assert len(rig.queue) <= 1
    rig.flush()
    assert caplog.text.count('Recording overlay unavailable') == 1


def test_factory_and_dispatch_failures_are_optional(rig):
    rig.overlay.factory = Mock(side_effect=RuntimeError('construction'))
    rig.start()
    rig.flush()
    assert rig.overlay.failed
    with rig.overlay.capture_guard(timeout=0) as hidden:
        assert not hidden  # failure must never assert pixel exclusion
    other = Overlay(True, Mock(side_effect=RuntimeError('dispatch')), lambda: Backend())
    other.begin(1)
    other.listening(1, None, 'batch', rig.recorder)
    assert other.failed and other.recorder is None


def test_headless_and_linux_never_import_appkit():
    code = '''
import os
import sys
from vox.ui.overlay import create_overlay
os.environ.pop('DISPLAY', None)
for platform, dispatch in [('linux', lambda fn: fn()), ('darwin', None), ('linux', None)]:
    sys.platform = platform
    overlay = create_overlay(True, dispatch)
    overlay.begin(1)
    overlay.listening(1, None, 'batch', None)
    overlay.close()
    with overlay.capture_guard() as hidden:
        assert hidden
assert 'AppKit' not in sys.modules
assert 'vox.ui.mac.overlay' not in sys.modules
assert 'vox.ui.x11.overlay' not in sys.modules
'''
    subprocess.run([sys.executable, '-c', code], check=True)


def test_screen_geometry_primary_secondary_spanning_and_small():
    screens = [(1, (0, 0, 1440, 900), (0, 70, 1440, 805)),
               (2, (-1920, 0, 1920, 1080), (-1920, 0, 1920, 1055))]
    assert panel_frame(screens) == (1, (608, 94, 224, 44))
    assert panel_frame(screens, (-1800, 200, 800, 600)) == (2, (-1072, 24, 224, 44))
    assert panel_frame(screens, (-600, 100, 800, 700))[0] == 2
    assert panel_frame(screens, (-100, 100, 800, 700))[0] == 1
    assert panel_frame([(1, (0, 0, 100, 40), (0, 0, 100, 40))]) == (1, (0, 0, 100, 40))
    assert quartz_to_appkit((-1920, -180, 800, 600), 900) == (-1920, 480, 800, 600)
    # Backing scales never enter logical-point geometry; offscreen/missing displays fall back.
    assert panel_frame(screens[:1], (-1800, 200, 800, 600))[0] == 1
    assert panel_frame([(1, (0, 0, 1920, 1080), (0, 30, 1920, 1010))], top_left=True) == (1, (848, 972, 224, 44))
    assert panel_frame([(1, (-200, -100, 100, 40), (-200, -100, 100, 40))], top_left=True) == (1, (-200, -100, 100, 40))


def test_broken_dispatch_after_show_requests_independent_main_thread_cleanup(rig):
    rig.start()
    rig.flush()
    backend = rig.made[0]
    tick = backend.callback
    backend.request_close = Mock()
    rig.overlay.dispatch = Mock(side_effect=RuntimeError('dispatch failed'))
    rig.overlay.dismiss()
    assert rig.overlay.failed
    backend.request_close.assert_called_once()
    tick()  # a surviving native tick is a second opportunity to release resources
    assert backend.closed and backend.callback is None


def test_default_off_capture_does_not_depend_on_gui_dispatch(rig):
    rig.overlay.set_enabled(False)
    with rig.overlay.capture_guard(timeout=0) as hidden:
        assert hidden
        rig.overlay.set_enabled(True)
        rig.start()
        rig.flush()
        assert not rig.made
    rig.flush()
    assert rig.made[0].visible


def test_processing_motion_starts_at_stop_and_restarts_for_next_recording(rig):
    rig.start()
    rig.flush()
    rig.now[0] = 123.0
    rig.overlay.processing(1)
    rig.flush()
    backend = rig.made[0]
    assert backend.frames[-1][:2] == (0, 0)
    rig.now[0] += 0.75
    backend.callback()
    assert backend.frames[-1][:2] == (0, 0.75)
    backend.motion(True)
    rig.flush()
    assert backend.frames[-1][:2] == (0, 0)
    assert backend.callback is None
    backend.motion(False)
    rig.flush()
    assert backend.frames[-1][1] == 0.75
    rig.start(2)
    rig.flush()
    rig.overlay.processing(2)
    rig.flush()
    assert backend.frames[-1][:2] == (0, 0)


def test_quiet_speech_is_visible_without_amplifying_silence():
    envelope = Envelope()
    for tick in range(10):
        now = tick / 30
        assert envelope.sample(now, (1, now, 0.0008), 1) == 0
    # Roughly -46 dBFS: previously this produced only 2.4% of the available height.
    heights = [envelope.sample(t / 30, (1, t / 30, 0.005), 1) for t in range(10, 14)]
    assert heights[0] > 0.2  # visible attack within the first frame
    assert 0.35 < heights[-1] < 0.5
    louder = envelope.sample(14 / 30, (1, 14 / 30, 0.02), 1)
    assert heights[-1] < louder < 1
    # A pause returns to the baseline; stale/other-session levels remain covered above.
    for tick in range(15, 46):
        quiet = envelope.sample(tick / 30, (1, tick / 30, 0), 1)
    assert quiet < 0.001


def test_waveform_sensitivity_is_bounded_for_loud_input():
    envelope = Envelope()
    for tick in range(60):
        now = tick / 30
        height = envelope.sample(now, (1, now, 1), 1)
        assert 0 <= height <= 1
    assert height > 0.99


def test_listening_drift_keeps_height_driven_by_microphone(rig):
    rig.start()
    rig.flush()
    backend = rig.made[0]
    for now in (2, 3, 4):
        rig.now[0] = now
        rig.recorder.latest_level = (1, now, 0.005)
        backend.callback()
        assert backend.frames[-1][1] == now
    quiet_height = backend.frames[-1][0]
    rig.now[0] = 5
    rig.recorder.latest_level = (1, 5, 0.02)
    backend.callback()
    assert backend.frames[-1][0] > quiet_height
    assert backend.frames[-1][1] == 5
    rig.now[0] = 7
    rig.recorder.latest_level = (1, 7, 0)
    backend.callback()
    assert backend.frames[-1][0] < 0.001
    rig.overlay.processing(1)
    rig.flush()
    rig.now[0] = 7.5
    backend.callback()
    assert backend.frames[-1][1] == 0.5  # the explicit waiting indicator still moves


def test_cancel_callback_dismisses_once_and_rejects_old_clicks(rig):
    cancelled = Mock()
    rig.overlay.on_cancel = cancelled
    rig.start()
    rig.flush()
    rig.made[0].on_cancel(1)
    assert rig.overlay.snapshot.phase == 'hidden'
    rig.flush()
    assert not rig.made[0].visible
    rig.made[0].on_cancel(1)
    cancelled.assert_called_once_with(1)
    rig.start(2)
    rig.flush()
    rig.made[0].on_cancel(1)
    assert rig.overlay.snapshot.phase == 'listening'
    rig.overlay.processing(2)
    rig.flush()
    rig.made[0].on_cancel(2)
    assert cancelled.call_count == 2
    assert rig.overlay.snapshot.phase == 'hidden'


def test_cancel_ignored_during_capture_and_fade(rig):
    rig.overlay.on_cancel = Mock()
    rig.start()
    rig.flush()
    with rig.overlay.capture_guard(timeout=0):
        rig.overlay.request_cancel(1)
    assert rig.overlay.snapshot.phase == 'listening'
    rig.overlay.complete(1)
    rig.overlay.request_cancel(1)
    rig.overlay.on_cancel.assert_not_called()


def test_late_tick_decays_only_after_level_expiry():
    dense, sparse = Envelope(), Envelope()
    level = (1, 1.0, 0.05)
    dense.sample(1, level, 1)
    sparse.sample(1, level, 1)
    for tick in range(1, 62):
        dense.sample(1 + tick / 100, level, 1)
    height = sparse.sample(1.61, level, 1)
    assert height > 0.9
    assert height == pytest.approx(dense.value)
    assert sparse.sample(2.6, level, 1) < 0.001


def test_meter_repeated_and_backwards_times_do_not_double_count():
    envelope = Envelope()
    level = (1, 1, 0.05)
    height = envelope.sample(1, level, 1)
    assert envelope.sample(1, level, 1) == height
    assert envelope.sample(0.99, level, 1) == height
    assert envelope.sample(1, level, 1) == height
    assert envelope.sample(1.03, level, 1) > height


@pytest.mark.parametrize('level', [(2, 1, 1), (1, 2, 1), (1, 1, float('nan')), (1, 1, float('inf'))])
def test_invalid_or_wrong_session_levels_do_not_drive_meter(level):
    assert Envelope().sample(1, level, 1) == 0


def test_overlay_reads_level_before_clock_and_recovers_after_staleness(rig):
    rig.start()
    rig.flush()
    backend = rig.made[0]
    rig.recorder.latest_level = (1, 1, 0.05)

    def clock_with_concurrent_publication():
        # Publication after the GUI reads its clock must belong to the next tick.
        rig.recorder.latest_level = (1, 10, 0.05)
        return 1.03

    rig.overlay.clock = clock_with_concurrent_publication
    backend.callback()
    assert backend.frames[-1][0] > 0.5
    rig.overlay.clock = lambda: rig.now[0]
    rig.recorder.latest_level = (1, 1, 0.05)
    rig.now[0] = 3
    backend.callback()
    assert backend.frames[-1][0] < 0.001
    rig.recorder.latest_level = (1, 3.04, 0.05)
    rig.now[0] = 3.04
    backend.callback()
    assert backend.frames[-1][0] > 0.5
