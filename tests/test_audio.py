"""Unit tests for audio capture, 24kHz conversion, and streaming chunk pipeline."""

import asyncio
import io
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vox.audio import Recorder, has_speech, to_pcm24k
from vox.config import Config


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
        recorder.start(loop=loop)
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
