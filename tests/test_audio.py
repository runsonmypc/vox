"""Unit tests for audio capture, 24kHz conversion, and streaming chunk pipeline."""

import asyncio
import io
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vox.audio import (
    STREAM_RATE,
    Recorder,
    Resampler,
    has_speech,
    is_digital_silence,
    is_silent,
    pcm16_wav,
    read_wav,
    resolve_input_device,
    split_at_pauses,
    upload_wavs,
)
from vox.config import Config
from vox.errors import AudioError


def _one_shot(samples, src, dst):
    resampler = Resampler(src, dst)
    out = np.concatenate([resampler.process(samples), resampler.flush()])
    return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


def _drain(queue):
    """Everything queued before the end marker, as int16 samples."""
    chunks = []
    while (chunk := queue.get_nowait()) is not None:
        chunks.append(chunk)
    return chunks, np.frombuffer(b"".join(chunks), dtype="<i2")


@pytest.mark.anyio
@pytest.mark.parametrize(("rate", "channels"), [(48000, 1), (48000, 2), (44100, 1), (24000, 1), (16000, 1)])
async def test_streamed_audio_is_24k_mono_pcm16_that_joins_up_seamlessly(rate, channels):
    """50 ms blocks go out as they arrive; together they equal converting the whole recording at once."""
    recorder = Recorder(Config(sample_rate=rate, channels=channels))
    frames = rate // 20
    t = np.arange(frames * 6) / rate
    audio = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
    blocks = np.repeat(audio[:, None], channels, axis=1)
    with patch("vox.audio.sd.InputStream", return_value=FakeStream()):
        recorder.start(loop=asyncio.get_running_loop(), stream=True)
        for i in range(6):
            recorder._callback(blocks[i * frames : (i + 1) * frames], frames, None, 0)
        recorder.stop()
        await asyncio.sleep(0)

    chunks, streamed = _drain(recorder.get_chunk_queue())
    assert len(chunks) >= 6  # one per block as it arrives, then the filter's last few samples
    assert len(streamed) == -(-len(audio) * STREAM_RATE // rate)
    assert np.abs(streamed.astype(int) - _one_shot(audio, rate, STREAM_RATE)).max() <= 1
    if rate == STREAM_RATE:
        np.testing.assert_array_equal(streamed, audio)


@pytest.mark.anyio
async def test_streamed_stereo_is_the_mean_of_the_channels():
    recorder = Recorder(Config(sample_rate=48000, channels=2))
    stereo = np.zeros((4800, 2), dtype=np.int16)
    stereo[:, 0] = 1000
    stereo[:, 1] = 3000
    with patch("vox.audio.sd.InputStream", return_value=FakeStream()):
        recorder.start(loop=asyncio.get_running_loop(), stream=True)
        recorder._callback(stereo, 4800, None, 0)
        recorder.stop()
        await asyncio.sleep(0)
    _, streamed = _drain(recorder.get_chunk_queue())
    assert len(streamed) == 2400
    assert set(streamed.tolist()) == {2000}


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

        # The consumer got the three blocks at 24 kHz (a chunk each, then the filter's tail):
        # 14400 samples at 48 kHz make 7200, the same as converting the recording in one go
        assert len(collected_chunks) == 4
        streamed = np.frombuffer(b"".join(collected_chunks), dtype="<i2")
        expected = _one_shot(np.concatenate([chunk1, chunk2, chunk3]), 48000, 24000)
        assert len(streamed) == 7200
        assert np.abs(streamed.astype(int) - expected).max() <= 1

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


@pytest.mark.anyio
async def test_a_live_session_that_gives_up_stops_the_queue_but_not_the_recording():
    recorder = Recorder(Config(sample_rate=16000))
    with patch("vox.audio.sd.InputStream", return_value=FakeStream()):
        recorder.start(loop=asyncio.get_running_loop(), stream=True)
        for value in (1, 2):
            recorder._callback(_block(value), 800, None, 0)
        await asyncio.sleep(0)
        assert recorder.get_chunk_queue().qsize() == 2

        recorder.stop_streaming()
        assert recorder.get_chunk_queue().empty()  # nobody will read what was queued
        recorder._callback(_block(3), 800, None, 0)
        await asyncio.sleep(0)
        assert recorder.get_chunk_queue().empty()  # nor what comes after

        samples, _ = _wav_samples(recorder.stop())
        await asyncio.sleep(0)
    assert list(np.unique(samples)) == [1, 2, 3]  # the batch fallback still gets all of it
    assert recorder.get_chunk_queue().empty()


@pytest.mark.anyio
async def test_a_discarded_recording_does_not_end_the_next_one_s_live_stream():
    """A cancel and the next start can run in one loop turn, before the discarded recording's
    tail and end marker, posted through the loop, have landed."""
    recorder = Recorder(Config(sample_rate=48000))
    loop = asyncio.get_running_loop()
    streamed = []

    async def consume():
        async for chunk in recorder.stream_chunks():
            streamed.append(chunk)

    with patch("vox.audio.sd.InputStream", side_effect=[FakeStream(), FakeStream()]):
        recorder.start(loop=loop, stream=True)
        recorder._callback(_block(1, 2400), 2400, None, 0)
        await asyncio.sleep(0)
        recorder.discard()
        recorder.start(loop=loop, stream=True)
        consumer = asyncio.create_task(consume())
        for value in (2, 3, 4):
            recorder._callback(_block(value, 2400), 2400, None, 0)
            await asyncio.sleep(0)
        recorder.stop()
        await consumer

    assert len(np.frombuffer(b"".join(streamed), dtype="<i2")) == 3600  # all of the second recording


@pytest.mark.anyio
async def test_the_block_delivered_while_stopping_streams_before_the_end_marker():
    """The last PortAudio block is posted through the loop, so the end marker must be too."""
    recorder = Recorder(Config(sample_rate=48000))
    closing = FakeStream(on_stop=lambda: recorder._callback(_block(3, 2400), 2400, None, 0))
    with patch("vox.audio.sd.InputStream", return_value=closing):
        recorder.start(loop=asyncio.get_running_loop(), stream=True)
        recorder._callback(_block(1, 2400), 2400, None, 0)
        recorder.stop()
        await asyncio.sleep(0)
    _, streamed = _drain(recorder.get_chunk_queue())
    assert len(streamed) == 2400  # 4800 frames at 48 kHz, the last block included


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


def test_has_speech_converts_44k_audio_to_16k_for_the_vad():
    rate = 44100
    word = (np.sin(2 * np.pi * 300 * np.arange(int(rate * 0.2)) / rate) * 60).astype(np.int16)
    assert has_speech(_wav(word, rate))
    assert not has_speech(_wav(np.zeros(rate), rate))
    with patch("vox.audio.webrtcvad.Vad") as vad:
        vad.return_value.is_speech.return_value = False
        assert not has_speech(_wav(word, rate))
    calls = vad.return_value.is_speech.call_args_list
    assert len(calls) == 6  # 200 ms is 3200 samples at 16 kHz: six whole 30 ms frames
    assert {(len(c.args[0]), c.args[1]) for c in calls} == {(960, 16000)}


def test_has_speech_gives_the_vad_mono_frames_of_a_stereo_recording():
    rate = 48000
    tone = (np.sin(2 * np.pi * 300 * np.arange(int(rate * 0.2)) / rate) * 3000).astype(np.int16)
    with patch("vox.audio.webrtcvad.Vad") as vad:
        vad.return_value.is_speech.return_value = False
        assert not has_speech(_wav(np.stack([tone, tone], axis=1).ravel(), rate, channels=2))
    calls = vad.return_value.is_speech.call_args_list
    assert len(calls) == 6  # 200 ms of audio, not 400 ms of interleaved samples
    assert calls[0].args == (tone[:1440].tobytes(), 48000)


def test_has_speech_assumes_speech_when_detection_breaks(caplog):
    assert has_speech(b"not a wav") is True
    assert "transcribing anyway" in caplog.text


def _level(samples, freq, rate):
    """Amplitude of the ``freq`` Hz component, over the middle 80%."""
    x = np.asarray(samples, dtype=np.float64)[len(samples) // 10 : -len(samples) // 10]
    return 2 * abs(np.dot(x, np.exp(-2j * np.pi * freq * np.arange(len(x)) / rate))) / len(x)


@pytest.mark.parametrize("rate", [48000, 44100])
def test_upload_wavs_downsamples_to_16k_mono_without_aliasing(rate):
    t = np.arange(rate * 2) / rate
    # A voice-band tone, plus a 12 kHz one that plain decimation would fold down to 4 kHz
    audio = (np.sin(2 * np.pi * 1000 * t) * 8000 + np.sin(2 * np.pi * 12000 * t) * 8000).astype(np.int16)
    [upload] = upload_wavs(_wav(audio, rate))
    with wave.open(io.BytesIO(upload), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getsampwidth(), wf.getnframes()) == (16000, 1, 2, 32000)
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    assert _level(samples, 1000, 16000) == pytest.approx(8000, rel=0.02)
    assert _level(samples, 4000, 16000) < 80  # 40 dB down

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


@pytest.mark.parametrize(("rate", "channels"), [(16000, 1), (44100, 2)])
def test_is_silent_needs_every_second_to_be_quiet(rate, channels):
    frames = rate * 90  # long enough to span several energy blocks
    hiss = np.random.default_rng(1).normal(0, 5, frames * channels)
    assert is_silent(_wav(hiss, rate, channels))
    # Half a second of soft sound anywhere, even at the very end, makes it audible
    for start in (0, frames * channels // 2, frames * channels - rate * channels // 2):
        loud = hiss.copy()
        loud[start : start + rate * channels // 2] += 60
        assert not is_silent(_wav(loud, rate, channels)), start
    assert not is_silent(b"not a wav")


def test_read_wav_returns_a_view_of_vox_s_own_wavs():
    """No copy of the samples: for an hour at 48 kHz that is 346 MB, made by every reader."""
    stereo = np.arange(-4800, 4800, dtype=np.int16)
    wav = pcm16_wav(stereo, 48000, channels=2)
    samples, rate, channels = read_wav(wav)
    assert (rate, channels) == (48000, 2)
    np.testing.assert_array_equal(samples, stereo)
    assert np.shares_memory(samples, np.frombuffer(wav, dtype=np.uint8))
    assert not samples.flags.writeable

    samples, _, _ = read_wav(pcm16_wav(np.empty(0, dtype=np.int16), 16000))
    assert len(samples) == 0


def test_read_wav_copies_when_another_chunk_follows_the_data():
    audio = np.arange(1600, dtype=np.int16)
    wav = pcm16_wav(audio, 16000)
    info = b"INFOISFT" + (4).to_bytes(4, "little") + b"Vox\0"
    tail = b"LIST" + len(info).to_bytes(4, "little") + info
    wav = wav[:4] + (len(wav) - 8 + len(tail)).to_bytes(4, "little") + wav[8:] + tail
    samples, rate, channels = read_wav(wav)
    assert (rate, channels) == (16000, 1)
    np.testing.assert_array_equal(samples, audio)


@pytest.mark.parametrize('channels', [1, 2])
def test_optional_level_uses_latest_block_and_resets(channels):
    recorder = Recorder(Config(sample_rate=16000, channels=channels))
    with patch('vox.audio.sd.InputStream', return_value=FakeStream()):
        recorder.start()
        recorder._callback(np.full((800, channels), 32767, dtype=np.int16), 800, None, 0)
        assert recorder.latest_level is None
        recorder.set_level_generation(7)
        for value in (0, 100, 2000, -32768, 0):
            block = np.full((800, channels), value, dtype=np.int16)
            recorder._callback(block, 800, None, 0)
            generation, timestamp, level = recorder.latest_level
            assert generation == 7 and timestamp > 0
            assert level == pytest.approx(abs(value) / 32768)
        recorder.stop()
        assert recorder.latest_level is None
        recorder.start()
        assert recorder.latest_level is None and recorder._level_generation is None
        recorder.set_level_generation(8)
        recorder.discard()
        assert recorder.latest_level is None and recorder._level_generation is None


@pytest.mark.anyio
async def test_meter_failure_cannot_drop_recorded_or_streamed_audio(caplog):
    recorder = Recorder(Config(sample_rate=24000))
    block = np.full((1200, 1), -32768, dtype=np.int16)
    with patch('vox.audio.sd.InputStream', return_value=FakeStream()):
        recorder.start(loop=asyncio.get_running_loop(), stream=True)
        recorder.set_level_generation(1)
        with patch('vox.audio._rms', side_effect=RuntimeError('optional measurement')):
            for _ in range(3):
                recorder._callback(block, 1200, None, 0)
        wav = recorder.stop()
        await asyncio.sleep(0)
    _, streamed = _drain(recorder.get_chunk_queue())
    np.testing.assert_array_equal(streamed, np.full(3600, -32768, dtype=np.int16))
    np.testing.assert_array_equal(read_wav(wav)[0], streamed)
    assert caplog.text.count('metering unavailable') == 1


@pytest.mark.parametrize('level', [float('nan'), float('inf')])
def test_nonfinite_meter_is_disabled_without_losing_audio(level):
    recorder = Recorder(Config(sample_rate=16000))
    with patch('vox.audio.sd.InputStream', return_value=FakeStream()):
        recorder.start()
        recorder.set_level_generation(1)
        with patch('vox.audio._rms', return_value=level):
            recorder._callback(_block(100), 800, None, 0)
        assert recorder.latest_level is None and recorder._meter_failed
        assert len(read_wav(recorder.stop())[0]) == 800
