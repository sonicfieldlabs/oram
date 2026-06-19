"""tests for offline DSP transforms — output shape, duration, no NaN/Inf."""

from __future__ import annotations

import numpy as np
import pytest

from oram.dsp.fades import fade_in, fade_out, trim_end, trim_start
from oram.dsp.filter import highpass, lowpass
from oram.dsp.granular import granular, stretch_breathe
from oram.dsp.pitch import pitch_shift
from oram.dsp.reverb import reverb, spatial_far
from oram.dsp.reverse import reverse
from oram.dsp.safety import crossfade_from_reference, prepare_dsp_output
from oram.dsp.speed import change_speed

SR = 48000


@pytest.fixture
def stereo_buffer():
    """a 1-second stereo test buffer."""
    t = np.linspace(0, 1, SR, dtype=np.float32)
    left = np.sin(2 * np.pi * 440 * t) * 0.5
    right = np.sin(2 * np.pi * 550 * t) * 0.5
    return np.column_stack([left, right])


@pytest.fixture
def mono_buffer():
    """a 1-second mono test buffer."""
    t = np.linspace(0, 1, SR, dtype=np.float32)
    return np.sin(2 * np.pi * 440 * t).astype(np.float32) * 0.5


def assert_no_nans(buf: np.ndarray, name: str = "buffer"):
    assert not np.any(np.isnan(buf)), f"{name} contains NaN"
    assert not np.any(np.isinf(buf)), f"{name} contains Inf"


class TestReverse:
    def test_preserves_shape(self, stereo_buffer):
        result = reverse(stereo_buffer)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "reverse")

    def test_double_reverse_identity(self, stereo_buffer):
        result = reverse(reverse(stereo_buffer))
        np.testing.assert_array_almost_equal(result, stereo_buffer)


class TestSpeed:
    def test_half_speed_doubles_length(self, stereo_buffer):
        result = change_speed(stereo_buffer, 0.5, SR)
        assert result.shape[0] == pytest.approx(stereo_buffer.shape[0] * 2, rel=0.01)
        assert result.shape[1] == 2
        assert_no_nans(result, "speed_half")

    def test_double_speed_halves_length(self, stereo_buffer):
        result = change_speed(stereo_buffer, 2.0, SR)
        assert result.shape[0] == pytest.approx(stereo_buffer.shape[0] / 2, rel=0.01)
        assert_no_nans(result, "speed_double")

    def test_common_fx_ratios_keep_exact_lengths(self, stereo_buffer):
        slow = change_speed(stereo_buffer, 0.5, SR)
        fast = change_speed(stereo_buffer, 2.0, SR)

        assert slow.shape == (stereo_buffer.shape[0] * 2, 2)
        assert fast.shape == (stereo_buffer.shape[0] // 2, 2)
        np.testing.assert_allclose(slow[0], stereo_buffer[0], atol=1e-6)
        np.testing.assert_allclose(fast[0], stereo_buffer[0], atol=1e-6)

    def test_unity_speed(self, stereo_buffer):
        result = change_speed(stereo_buffer, 1.0, SR)
        assert result.shape == stereo_buffer.shape
        np.testing.assert_array_almost_equal(result, stereo_buffer)


class TestPitch:
    def test_pitch_up(self, stereo_buffer):
        result = pitch_shift(stereo_buffer, 5.0, SR)
        assert result.shape[0] < stereo_buffer.shape[0]
        assert result.shape[1] == 2
        assert_no_nans(result, "pitch_up")

    def test_pitch_down(self, stereo_buffer):
        result = pitch_shift(stereo_buffer, -5.0, SR)
        assert result.shape[0] > stereo_buffer.shape[0]
        assert_no_nans(result, "pitch_down")

    def test_zero_pitch(self, stereo_buffer):
        result = pitch_shift(stereo_buffer, 0.0, SR)
        assert result.shape == stereo_buffer.shape


class TestFilter:
    def test_lowpass(self, stereo_buffer):
        result = lowpass(stereo_buffer, 1000, SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "lowpass")

    def test_highpass(self, stereo_buffer):
        result = highpass(stereo_buffer, 4000, SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "highpass")

    def test_mono_lowpass(self, mono_buffer):
        result = lowpass(mono_buffer, 1000, SR)
        assert result.shape == mono_buffer.shape
        assert_no_nans(result, "mono_lowpass")


class TestReverb:
    def test_reverb_preserves_shape(self, stereo_buffer):
        result = reverb(stereo_buffer, wet=0.3, decay="medium", sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "reverb")

    def test_dry_reverb(self, stereo_buffer):
        result = reverb(stereo_buffer, wet=0.0, sample_rate=SR)
        np.testing.assert_array_almost_equal(result, stereo_buffer, decimal=5)

    def test_spatial_far(self, stereo_buffer):
        result = spatial_far(stereo_buffer, SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "spatial_far")
        # should have lower RMS (quieter overall)
        result_rms = float(np.sqrt(np.mean(result**2)))
        input_rms = float(np.sqrt(np.mean(stereo_buffer**2)))
        assert result_rms < input_rms


class TestFades:
    def test_fade_in(self, stereo_buffer):
        result = fade_in(stereo_buffer, 0.5, SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "fade_in")
        # first sample should be near zero
        assert np.max(np.abs(result[0])) < 0.01

    def test_fade_out(self, stereo_buffer):
        result = fade_out(stereo_buffer, 0.5, SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "fade_out")
        # last sample should be near zero
        assert np.max(np.abs(result[-1])) < 0.01

    def test_trim_start(self):
        buf = np.zeros((48000, 2), dtype=np.float32)
        buf[10000:] = np.random.randn(38000, 2).astype(np.float32) * 0.5
        result = trim_start(buf)
        assert result.shape[0] < buf.shape[0]
        assert_no_nans(result, "trim_start")

    def test_trim_end(self):
        buf = np.zeros((48000, 2), dtype=np.float32)
        buf[:30000] = np.random.randn(30000, 2).astype(np.float32) * 0.5
        result = trim_end(buf)
        assert result.shape[0] < buf.shape[0]
        assert_no_nans(result, "trim_end")


class TestGranular:
    def test_granular_preserves_shape(self, stereo_buffer):
        result = granular(stereo_buffer, density=0.3, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "granular")

    def test_granular_soft(self, stereo_buffer):
        result = granular(stereo_buffer, density=0.3, jitter=0.15, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "granular_soft")

    def test_granular_heavy(self, stereo_buffer):
        result = granular(stereo_buffer, density=0.7, jitter=0.5, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "granular_heavy")

    def test_stretch_breathe(self, stereo_buffer):
        result = stretch_breathe(stereo_buffer, SR)
        # should be longer (slowed down)
        assert result.shape[0] > stereo_buffer.shape[0]
        assert result.shape[1] == 2
        assert_no_nans(result, "stretch_breathe")


class TestDSPSafety:
    def test_prepare_dsp_output_repairs_bad_samples_and_limits_peak(self):
        buf = np.ones((1000, 2), dtype=np.float32) * 3.0
        buf[20, 0] = np.nan
        buf[21, 1] = np.inf

        result = prepare_dsp_output(buf, sample_rate=SR)

        assert result.shape == buf.shape
        assert_no_nans(result, "prepared")
        assert float(np.max(np.abs(result))) <= 0.98 + 1e-6
        assert np.max(np.abs(result[0])) == pytest.approx(0.0)
        assert np.max(np.abs(result[-1])) == pytest.approx(0.0)

    def test_crossfade_from_reference_starts_with_old_audio(self):
        processed = np.ones((100, 2), dtype=np.float32) * 0.8
        reference = np.zeros((100, 2), dtype=np.float32)
        reference[40:] = 0.2

        result = crossfade_from_reference(
            processed,
            reference,
            processed_start=40,
            reference_start=40,
            sample_rate=1000,
            fade_ms=10.0,
        )

        np.testing.assert_allclose(result[40], reference[40], atol=1e-6)
        np.testing.assert_allclose(result[49], processed[49], atol=1e-6)
