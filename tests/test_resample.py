"""The band-limited resampler behind uploads, VAD, whisper.cpp input and the live stream."""

import tracemalloc

import numpy as np
import pytest

from vox.audio import Resampler, to_16k_mono


def _tone(freq, rate, seconds=1.0, amplitude=10000.0):
    return amplitude * np.sin(2 * np.pi * freq * np.arange(int(rate * seconds)) / rate)


def _convert(samples, src, dst):
    resampler = Resampler(src, dst)
    return np.concatenate([resampler.process(samples), resampler.flush()])


def _gain_db(output, source):
    """Output level against the input's, over the middle 80% (away from the ends)."""
    def rms(x):
        x = np.asarray(x, dtype=np.float64)[len(x) // 10 : -len(x) // 10]
        return np.sqrt(np.mean(np.square(x)))
    return 20 * np.log10(rms(output) / rms(source))


@pytest.mark.parametrize(("src", "dst"), [(48000, 16000), (48000, 24000), (44100, 16000), (44100, 24000)])
def test_speech_band_tone_keeps_its_level(src, dst):
    source = _tone(1000, src)
    output = _convert(source, src, dst)
    assert abs(_gain_db(output, source)) < 1.0
    # and it is still a 1 kHz tone at the new rate
    np.testing.assert_allclose(output[dst // 10 : -dst // 10], _tone(1000, dst)[dst // 10 : -dst // 10], atol=10)


@pytest.mark.parametrize(("freq", "dst", "alias"), [(12000, 16000, 4000), (15000, 24000, 9000)])
def test_tone_above_the_new_band_is_removed_not_aliased(freq, dst, alias):
    source = _tone(freq, 48000)
    decimated = source[:: 48000 // dst]  # what keeping every nth sample did
    assert _gain_db(decimated, source) > -1  # full strength, folded down to `alias` Hz
    assert _gain_db(_convert(source, 48000, dst), source) < -40


def test_tone_above_the_new_band_is_removed_from_44k_audio():
    source = _tone(12000, 44100)
    assert _gain_db(_convert(source, 44100, 16000), source) < -40


@pytest.mark.parametrize(("src", "dst"), [(48000, 16000), (48000, 24000), (44100, 16000), (44100, 24000), (16000, 24000)])
def test_chunked_output_equals_one_shot_output(src, dst):
    rng = np.random.default_rng(src + dst)
    source = (rng.standard_normal(src * 2) * 3000).astype(np.float32)
    whole = _convert(source, src, dst)

    resampler = Resampler(src, dst)
    parts, start = [], 0
    for size in [0, 1, 7, 2400, 0, 1, 333] + list(rng.integers(0, 5000, 100)):
        parts.append(resampler.process(source[start : start + size]))
        start += size
    parts.append(resampler.process(source[start:]))
    parts.append(resampler.flush())

    np.testing.assert_allclose(np.concatenate(parts), whole, rtol=0, atol=0.02)


@pytest.mark.parametrize(("src", "dst"), [(48000, 16000), (48000, 24000), (44100, 16000), (44100, 24000), (16000, 24000)])
@pytest.mark.parametrize("frames", [0, 1, 2, 3, 100, 4410, 48001])
def test_output_length_is_the_input_duration_at_the_new_rate(src, dst, frames):
    resampler = Resampler(src, dst)
    expected = -(-frames * dst // src)
    assert resampler.output_length(frames) == expected
    assert len(_convert(np.ones(frames), src, dst)) == expected


def test_same_rate_passes_samples_through():
    samples = np.arange(-500, 500, dtype=np.int16)
    np.testing.assert_array_equal(_convert(samples, 16000, 16000), samples)
    assert len(Resampler(16000, 16000).flush()) == 0


def test_constant_signal_stays_constant_up_to_its_ends():
    for rate in (48000, 44100, 24000):
        np.testing.assert_array_equal(to_16k_mono(np.full(rate // 10, 1234, dtype=np.int16), rate), 1234)


def test_full_scale_input_is_clipped_not_wrapped():
    square = np.tile(np.r_[np.full(24, 32767), np.full(24, -32768)], 1000).astype(np.int16)
    out = to_16k_mono(square, 48000)
    assert out.max() == 32767 and out.min() == -32768  # the filter's overshoot saturates


def test_fifteen_minute_recording_converts_in_bounded_memory():
    rate = 48000
    second = (np.sin(2 * np.pi * 440 * np.arange(rate) / rate) * 8000).astype(np.int16)
    samples = np.tile(second, 15 * 60)

    tracemalloc.start()
    try:
        out = to_16k_mono(samples, rate)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(out) == 15 * 60 * 16000
    # Beyond the 29 MB result, only a few blocks' worth: not a float copy of the 86 MB input
    assert peak - out.nbytes < 8 * 1024 * 1024
    assert abs(_gain_db(out[:16000], second)) < 1.0
