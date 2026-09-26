"""Unit tests for audio capture, 24kHz conversion, and streaming chunk pipeline."""

import asyncio
import io
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vox.audio import (
    Recorder,
    has_speech,
    is_digital_silence,
    resolve_input_device,
    split_at_pauses,
    to_pcm24k,
    upload_wavs,
)
from vox.config import Config
from vox.errors import AudioError


def test_to_pcm24k_from_48k():
    """Verify 48kHz -> 24kHz decimation, chunk size, and 16-bit little-endian byte format."""
    orig_rate = 48000
    duration = 0.1  # 100ms
    num_samples = int(orig_rate * duration)  # 4800 samples
    # Create known 16-bit integer samples
    t = np.linspace(0, duration, num_samples, endpoint=False)
    # 440Hz sine wave scaled to int16 range
    sine_wave = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)

    pcm_bytes = to_pcm24k(sine_wave, orig_rate=orig_rate)

    # Output should have 2400 samples (4800 // 2)
    expected_samples = 2400
    expected_bytes = expected_samples * 2  # 2 bytes per sample (16-bit)
    assert len(pcm_bytes) == expected_bytes

    # Unpack as little-endian 16-bit signed integers
    unpacked = np.frombuffer(pcm_bytes, dtype="<i2")
    assert len(unpacked) == expected_samples

    # Verify decimation (every 2nd sample from input)
    np.testing.assert_array_equal(unpacked, sine_wave[::2])

    # Verify output chunk rate: 2400 samples / 0.1s = 24000 samples/sec
    effective_sample_rate = len(unpacked) / duration
    assert effective_sample_rate == 24000


def test_to_pcm24k_from_24k():
    orig_rate = 24000
    samples = np.array([100, -200, 300, -400], dtype=np.int16)
    pcm_bytes = to_pcm24k(samples, orig_rate=orig_rate)
    assert len(pcm_bytes) == 8
    unpacked = np.frombuffer(pcm_bytes, dtype="<i2")
    np.testing.assert_array_equal(unpacked, samples)


def test_to_pcm24k_from_16k():
    orig_rate = 16000
    duration = 0.1  # 100ms
    num_samples = int(orig_rate * duration)  # 1600 samples
    samples = np.full(num_samples, 500, dtype=np.int16)
    pcm_bytes = to_pcm24k(samples, orig_rate=orig_rate)
    # Resampled to 24kHz: 1600 * 24 / 16 = 2400 samples
    assert len(pcm_bytes) == 2400 * 2
    unpacked = np.frombuffer(pcm_bytes, dtype="<i2")
    assert len(unpacked) == 2400


def test_to_pcm24k_stereo_to_mono():
    orig_rate = 48000
    # Stereo: 4800 samples x 2 channels
    stereo = np.zeros((4800, 2), dtype=np.int16)
    stereo[:, 0] = 1000
    stereo[:, 1] = 3000
    pcm_bytes = to_pcm24k(stereo, orig_rate=orig_rate)
    unpacked = np.frombuffer(pcm_bytes, dtype="<i2")
    assert len(unpacked) == 2400
    # Mean of 1000 and 3000 is 2000
    assert unpacked[0] == 2000


def test_has_speech_silence():
    # 1 second of silence at 48kHz
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(np.zeros(48000, dtype=np.int16).tobytes())
    assert not has_speech(buf.getvalue())


@pytest.mark.anyio
async def test_recorder_streaming_and_buffer_retention():
    """Verify live streaming chunks consumption while preserving complete turn WAV in memory."""
    config = Config()
    config.sample_rate = 48000
    recorder = Recorder(config)

    mock_stream = MagicMock()
    mock_stream.active = True

    with patch("vox.audio.sd.InputStream", return_value=mock_stream):
        loop = asyncio.get_running_loop()
        recorder.start(loop=loop, stream=True)
        assert recorder.is_recording

        collected_chunks = []

        async def consume_stream():
            async for chunk in recorder.stream_chunks():
                collected_chunks.append(chunk)

        consumer_task = asyncio.create_task(consume_stream())

        # Simulate 3 audio callbacks (100ms at 48kHz = 4800 samples each)
        chunk1 = (np.ones(4800, dtype=np.int16) * 100)
        chunk2 = (np.ones(4800, dtype=np.int16) * 200)
        chunk3 = (np.ones(4800, dtype=np.int16) * 300)

        recorder._callback(chunk1, 4800, None, 0)
        recorder._callback(chunk2, 4800, None, 0)
        recorder._callback(chunk3, 4800, None, 0)

        # Allow consumer task to process chunks from queue
        await asyncio.sleep(0.01)

        # Stop recording
        wav_bytes = recorder.stop()
        await consumer_task

        # Verify stream consumer received all 3 chunks converted to 24kHz PCM16
        assert len(collected_chunks) == 3
        for chunk in collected_chunks:
            # 4800 samples at 48k decimated to 2400 samples at 24k = 4800 bytes
            assert len(chunk) == 4800
            samples_24k = np.frombuffer(chunk, dtype="<i2")
            assert len(samples_24k) == 2400

        # Verify complete turn audio buffer was preserved in memory as valid WAV
        assert len(wav_bytes) > 0
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 48000
            # Total frames: 4800 * 3 = 14400
            assert wf.getnframes() == 14400
            frames = wf.readframes(wf.getnframes())
            full_audio = np.frombuffer(frames, dtype=np.int16)
            assert len(full_audio) == 14400
            # Verify data contents preserved in memory
            np.testing.assert_array_equal(full_audio[:4800], chunk1)
            np.testing.assert_array_equal(full_audio[4800:9600], chunk2)
            np.testing.assert_array_equal(full_audio[9600:], chunk3)


def test_resolve_input_device_by_name_and_index():
    mock_devices = [
        {"name": "Alex’s iPhone 16 Microphone", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Multi-Output Device", "max_input_channels": 0, "max_output_channels": 0},
    ]

    with patch("vox.audio.sd.query_devices", return_value=mock_devices), \
         patch("vox.audio.sd.default.device", [1, 2]):

        # 1. Resolve by name substring (case-insensitive)
        assert resolve_input_device("MacBook Pro Microphone") == 1
        assert resolve_input_device("macbook") == 1
        assert resolve_input_device("iphone") == 0

        # 2. Resolve by valid index
        assert resolve_input_device(0) == 0
        assert resolve_input_device(1) == 1

        # 3. Handle device with 0 input channels gracefully (fallback to default input)
        # Device 3 is "Multi-Output Device" (0 in channels) - must NOT return 3!
        assert resolve_input_device(3) == 1
        # Device 2 is Speakers (0 in channels) - must NOT return 2!
        assert resolve_input_device(2) == 1

        # 4. Handle non-existent device name (fallback to default input)
        assert resolve_input_device("Unknown Mic") == 1

        # 5. Handle out of bounds index (fallback to default input)
        assert resolve_input_device(99) == 1

        # 6. None spec returns default input device
        assert resolve_input_device(None) == 1


def test_recorder_uses_resolved_device():
    mock_devices = [
        {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Built-in Mic", "max_input_channels": 1, "max_output_channels": 0},
    ]

    config = Config(audio_device="Built-in Mic")
    recorder = Recorder(config)

    mock_stream = MagicMock()
    with patch("vox.audio.sd.query_devices", return_value=mock_devices), \
         patch("vox.audio.sd.default.device", [1, 0]), \
         patch("vox.audio.sd.InputStream", return_value=mock_stream) as mock_input_stream:

        recorder.start()
        # Verify InputStream was opened with resolved device index 1
        mock_input_stream.assert_called_once()
        _, kwargs = mock_input_stream.call_args
        assert kwargs["device"] == 1


def test_has_speech_soft_and_short_utterances():
    """Verify that soft speech and short words (>=80ms) are detected and not falsely dropped."""
    sr = 16000
    duration = 0.2  # 200ms short word
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    # Scaled sine with amplitude 60 (RMS ~42, well above 20 threshold)
    samples = (np.sin(2 * np.pi * 300 * t) * 60).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())

    assert has_speech(buf.getvalue())




# -- Recorder: limit, memory, streaming only when asked --------------------------


@pytest.fixture(autouse=True)
def _no_real_devices():
    """Keep device resolution off the real audio hardware; tests that care patch it themselves."""
    with patch("vox.audio.sd.query_devices", return_value=[{"name": "Test Mic", "max_input_channels": 2}]):
        yield


class FakeStream:
    """An InputStream stand-in; stop() can deliver one last block, as PortAudio may."""

    def __init__(self, on_stop=None):
        self.active = False
        self._on_stop = on_stop

    def start(self):
        self.active = True

    def stop(self):
        if self._on_stop is not None:
            self._on_stop()
        self.active = False

    def close(self):
        pass


def _block(value, frames=800):
    return np.full((frames, 1), value, dtype=np.int16)


def _wav(samples, rate, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return buf.getvalue()


def _wav_samples(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16), wf.getframerate()


@pytest.mark.anyio
async def test_recorder_stops_buffering_at_the_limit_and_reports_it_once():
    recorder = Recorder(Config(sample_rate=16000, max_recording_seconds=1))
    limits = []
    with patch("vox.audio.sd.InputStream", return_value=FakeStream()):
        recorder.start(loop=asyncio.get_running_loop(), stream=True, on_limit=lambda: limits.append(True))
        for value in range(1, 26):  # 25 blocks of 50 ms: 1.25 s
            recorder._callback(_block(value), 800, None, 0)
        await asyncio.sleep(0)
        assert recorder.limit_reached
        assert limits == [True]
        queued = recorder.get_chunk_queue().qsize()
        samples, rate = _wav_samples(recorder.stop())

    assert rate == 16000
    assert len(samples) == 16000  # exactly the limit; nothing after it was kept
    assert samples[-1] == 20
    assert queued == 20  # the live stream stops at the same point


@pytest.mark.anyio
async def test_recorder_trims_the_block_that_crosses_the_limit():
    recorder = Recorder(Config(sample_rate=16000, max_recording_seconds=1))
    with patch("vox.audio.sd.InputStream", return_value=FakeStream()):
        recorder.start(loop=asyncio.get_running_loop())
        recorder._callback(_block(1, 15000), 15000, None, 0)
        recorder._callback(_block(2, 1500), 1500, None, 0)
        samples, _ = _wav_samples(recorder.stop())
    assert len(samples) == 16000
    assert (samples[15000:] == 2).all()


@pytest.mark.anyio
async def test_recorder_reads_the_limit_when_a_recording_starts():
    config = Config(sample_rate=16000, max_recording_seconds=1)
    recorder = Recorder(config)
    with patch("vox.audio.sd.InputStream", return_value=FakeStream()):
        recorder.start(loop=asyncio.get_running_loop())
        config.max_recording_seconds = 60  # picked from the menu mid-recording
        for value in range(30):
            recorder._callback(_block(value), 800, None, 0)
        assert len(_wav_samples(recorder.stop())[0]) == 16000

        recorder.start(loop=asyncio.get_running_loop())
        assert not recorder.limit_reached
        for value in range(30):
            recorder._callback(_block(value), 800, None, 0)
        assert len(_wav_samples(recorder.stop())[0]) == 24000
        assert not recorder.limit_reached


@pytest.mark.anyio
async def test_batch_recording_does_not_feed_the_stream_queue_and_stop_releases_audio():
    recorder = Recorder(Config(sample_rate=16000))
    with patch("vox.audio.sd.InputStream", return_value=FakeStream()):
        recorder.start(loop=asyncio.get_running_loop())
        recorder._callback(_block(5), 800, None, 0)
        await asyncio.sleep(0)
        assert recorder.get_chunk_queue().empty()
        recorder.stop()
        await asyncio.sleep(0)
        assert recorder.get_chunk_queue().empty()  # no end marker either: nobody reads it
    assert recorder._chunks == []


def test_stop_does_not_leak_audio_into_the_next_recording():
    """Replaces the old pre-roll test: the mic is closed between recordings, so nothing carries over."""
    recorder = Recorder(Config(sample_rate=16000))
    first = FakeStream(on_stop=lambda: recorder._callback(_block(7), 800, None, 0))
    with patch("vox.audio.sd.InputStream", side_effect=[first, FakeStream()]):
        recorder.start()
        recorder._callback(_block(1), 800, None, 0)
        samples, _ = _wav_samples(recorder.stop())
        assert list(np.unique(samples)) == [1, 7]  # the block delivered while stopping is kept

        recorder._callback(_block(9), 800, None, 0)  # a stray block between recordings is dropped
        recorder.start()
        recorder._callback(_block(2), 800, None, 0)
        samples, _ = _wav_samples(recorder.stop())
    assert list(np.unique(samples)) == [2]


def test_stop_without_audio_raises_and_the_recorder_still_works():
    recorder = Recorder(Config(sample_rate=16000))
    with patch("vox.audio.sd.InputStream", side_effect=[FakeStream(), FakeStream()]):
        recorder.start()
        with pytest.raises(AudioError):
            recorder.stop()
        recorder.start()
        recorder._callback(_block(4), 800, None, 0)
        assert len(_wav_samples(recorder.stop())[0]) == 800


def test_start_failure_leaves_the_recorder_idle():
    recorder = Recorder(Config(sample_rate=16000))
    with patch("vox.audio.sd.InputStream", side_effect=RuntimeError("no device")):
        with pytest.raises(AudioError):
            recorder.start()
    assert not recorder._is_recording
    recorder._callback(_block(3), 800, None, 0)
    assert recorder._chunks == []


def test_reconfigure_during_a_recording_waits_for_it_to_end():
    config = Config(sample_rate=48000, max_recording_seconds=30)
    recorder = Recorder(config)
    with patch("vox.audio.sd.InputStream", return_value=FakeStream()), patch.object(recorder, "warmup"):
        recorder.start()
        recorder._callback(_block(3, 2400), 2400, None, 0)
        recorder.reconfigure(Config(sample_rate=16000, max_recording_seconds=30))
        assert recorder._sample_rate == 48000
        samples, rate = _wav_samples(recorder.stop())
        assert (len(samples), rate) == (2400, 48000)  # the recording survived, at its own rate

        assert recorder._sample_rate == 16000
        recorder.start()
        assert recorder._max_frames == 30 * 16000  # the limit follows the new rate
        recorder.discard()


def test_refresh_input_devices_restarts_portaudio_only_when_idle():
    devices = [
        {"name": "Speakers", "max_input_channels": 0},
        {"name": "USB Mic", "max_input_channels": 1},
        {"name": "Built-in Mic", "max_input_channels": 2},
    ]
    recorder = Recorder(Config())
    recorder._resolved_device = 5
    with patch("vox.audio.sd.query_devices", return_value=devices), \
         patch("vox.audio.sd._initialized", 1), \
         patch("vox.audio.sd._terminate") as terminate, \
         patch("vox.audio.sd._initialize") as initialize, \
         patch("vox.audio.sd.stop") as stop_playback, \
         patch("vox.audio.sd.InputStream", return_value=FakeStream()):
        assert recorder.refresh_input_devices() == [(1, "USB Mic"), (2, "Built-in Mic")]
        stop_playback.assert_called_once()
        terminate.assert_called_once()
        initialize.assert_called_once()
        assert recorder._resolved_device is None  # indices may have moved

        recorder.start()
        assert recorder.refresh_input_devices() is None  # would close the open stream
        assert terminate.call_count == 1
        recorder.discard()


def test_resolve_input_device_prefers_an_exact_name():
    devices = [
        {"name": "USB Mic 2", "max_input_channels": 1},
        {"name": "USB Mic", "max_input_channels": 1},
    ]
    with patch("vox.audio.sd.query_devices", return_value=devices):
        assert resolve_input_device("USB Mic") == 1
        assert resolve_input_device("usb mic 2") == 0
        assert resolve_input_device("usb") == 0


# -- Speech detection and WAV conversion ---------------------------------------------


def test_has_speech_stops_at_the_first_80ms_of_speech():
    loud = (np.sin(np.linspace(0, 20000, 48000 * 60)) * 8000).astype(np.int16)
    with patch("vox.audio.webrtcvad.Vad") as vad:
        vad.return_value.is_speech.return_value = True
        assert has_speech(_wav(loud, 48000))
    assert vad.return_value.is_speech.call_count == 3  # ceil(80 / 30), not 2000 frames


def test_has_speech_assumes_speech_when_detection_breaks(caplog):
    assert has_speech(b"not a wav") is True
    assert "transcribing anyway" in caplog.text


def test_upload_wavs_downsamples_to_16k_mono():
    ramp = (np.arange(48000 * 3) % 30000).astype(np.int16)
    [upload] = upload_wavs(_wav(ramp, 48000))
    with wave.open(io.BytesIO(upload), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) == (16000, 1, 2)
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    np.testing.assert_array_equal(samples, ramp[::3])

    stereo = np.stack([np.full(4410, 1000), np.full(4410, 3000)], axis=1).astype(np.int16)
    [upload] = upload_wavs(_wav(stereo.ravel(), 44100, channels=2))
    with wave.open(io.BytesIO(upload), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getnframes()) == (16000, 1, 1600)
        assert set(np.frombuffer(wf.readframes(1600), dtype=np.int16).tolist()) == {2000}


def test_upload_wavs_splits_an_oversized_recording_in_a_pause():
    rate = 16000
    speech = (np.sin(np.linspace(0, 30000, rate * 10)) * 8000).astype(np.int16)
    speech[int(rate * 7.0):int(rate * 7.5)] = 0  # a half-second pause
    max_bytes = 44 + 2 * rate * 8  # room for 8 s per upload

    parts = upload_wavs(_wav(speech, rate), max_bytes=max_bytes)

    assert len(parts) == 2
    assert all(len(part) <= max_bytes for part in parts)
    first, second = (_wav_samples(part)[0] for part in parts)
    assert rate * 7.0 <= len(first) <= rate * 7.5  # cut inside the pause, not mid-word
    np.testing.assert_array_equal(np.concatenate([first, second]), speech)


def test_split_at_pauses_keeps_short_audio_whole_and_bounds_every_part():
    samples = np.ones(100, dtype=np.int16)
    [part] = split_at_pauses(samples, 16000, 1000)
    np.testing.assert_array_equal(part, samples)

    noise = np.random.default_rng(0).integers(-3000, 3000, 16000 * 30).astype(np.int16)
    parts = split_at_pauses(noise, 16000, 16000 * 7)
    assert all(len(p) <= 16000 * 7 for p in parts)
    np.testing.assert_array_equal(np.concatenate(parts), noise)


def test_is_digital_silence_flags_only_all_zero_audio():
    assert is_digital_silence(_wav(np.zeros(1600), 16000))
    assert not is_digital_silence(_wav(np.r_[np.zeros(1599), 1], 16000))
    assert not is_digital_silence(_wav([], 16000))
    assert not is_digital_silence(b"")
