"""tests for offline DSP transforms — output shape, duration, no NaN/Inf."""

from __future__ import annotations

import numpy as np
import pytest

from oram.dsp.bitcrush import bitcrush
from oram.dsp.chorus import chorus
from oram.dsp.delay import delay
from oram.dsp.distortion import distortion
from oram.dsp.fades import fade_in, fade_out, trim_end, trim_start
from oram.dsp.filter import bandpass, highpass, lowpass
from oram.dsp.flanger import flanger
from oram.dsp.granular import granular, stretch_breathe
from oram.dsp.normalize import normalize
from oram.dsp.phaser import phaser
from oram.dsp.pitch import pitch_shift
from oram.dsp.reverb import reverb, spatial_far
from oram.dsp.reverse import reverse
from oram.dsp.safety import crossfade_from_reference, prepare_dsp_output
from oram.dsp.spatial import spatial_near, spatial_wide
from oram.dsp.speed import change_speed
from oram.dsp.stretch import time_stretch
from oram.dsp.stutter import stutter

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

    def test_double_speed_transposes_without_aliasing(self, stereo_buffer):
        """2x speed of a 440 Hz tone must read as a clean 880 Hz tone."""
        fast = change_speed(stereo_buffer, 2.0, SR)
        mono = fast[:, 0]
        spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
        freqs = np.fft.rfftfreq(len(mono), 1.0 / SR)
        dominant = float(freqs[np.argmax(spec)])
        assert abs(dominant - 880.0) < 10.0

    def test_unity_speed(self, stereo_buffer):
        result = change_speed(stereo_buffer, 1.0, SR)
        assert result.shape == stereo_buffer.shape
        np.testing.assert_array_almost_equal(result, stereo_buffer)


class TestPitch:
    def test_pitch_up_preserves_duration(self, stereo_buffer):
        result = pitch_shift(stereo_buffer, 5.0, SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "pitch_up")

    def test_pitch_up_transposes_accurately(self, stereo_buffer):
        result = pitch_shift(stereo_buffer, 7.0, SR)
        mono = result[:, 0]
        spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
        freqs = np.fft.rfftfreq(len(mono), 1.0 / SR)
        dominant = float(freqs[np.argmax(spec)])
        target = 440.0 * 2 ** (7 / 12)
        cents = 1200.0 * np.log2(dominant / target)
        assert abs(cents) < 10.0

    def test_pitch_down_preserves_duration(self, stereo_buffer):
        result = pitch_shift(stereo_buffer, -5.0, SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "pitch_down")

    def test_pitch_legacy_varispeed_changes_duration(self, stereo_buffer):
        up = pitch_shift(stereo_buffer, 5.0, SR, preserve_duration=False)
        down = pitch_shift(stereo_buffer, -5.0, SR, preserve_duration=False)
        assert up.shape[0] < stereo_buffer.shape[0]
        assert down.shape[0] > stereo_buffer.shape[0]

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


class TestTimeStretch:
    def test_stretch_lengthens_without_transposing(self, stereo_buffer):
        result = time_stretch(stereo_buffer, 1.5, SR)
        assert abs(result.shape[0] - int(stereo_buffer.shape[0] * 1.5)) <= SR // 50
        mono = result[:, 0]
        spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
        freqs = np.fft.rfftfreq(len(mono), 1.0 / SR)
        dominant = float(freqs[np.argmax(spec)])
        assert abs(dominant - 440.0) < 8.0
        assert_no_nans(result, "time_stretch")

    def test_stretch_shorter(self, stereo_buffer):
        result = time_stretch(stereo_buffer, 0.7, SR)
        assert abs(result.shape[0] - int(stereo_buffer.shape[0] * 0.7)) <= SR // 50
        assert_no_nans(result, "time_stretch_short")

    def test_unity_ratio_is_copy(self, stereo_buffer):
        result = time_stretch(stereo_buffer, 1.0, SR)
        np.testing.assert_array_equal(result, stereo_buffer)


class TestNewEffects:
    """all new fx must keep shape (loop length) and stay finite."""

    def test_delay_keeps_shape(self, stereo_buffer):
        result = delay(stereo_buffer, time_ms=250, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "delay")

    def test_delay_produces_echo_energy(self):
        impulse = np.zeros((SR, 2), dtype=np.float32)
        impulse[0] = 0.9
        result = delay(impulse, time_ms=200, feedback=0.5, wet=0.5, sample_rate=SR)
        echo_at = int(0.2 * SR)
        assert np.max(np.abs(result[echo_at - 50:echo_at + 50])) > 0.05

    def test_delay_pingpong(self, stereo_buffer):
        result = delay(stereo_buffer, pingpong=True, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "delay_pingpong")

    def test_chorus(self, stereo_buffer):
        result = chorus(stereo_buffer, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "chorus")

    def test_flanger(self, stereo_buffer):
        result = flanger(stereo_buffer, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "flanger")

    def test_phaser(self, stereo_buffer):
        result = phaser(stereo_buffer, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "phaser")

    def test_distortion_characters(self, stereo_buffer):
        for character in ("soft", "warm", "fuzz"):
            result = distortion(stereo_buffer, drive=6.0, character=character, sample_rate=SR)
            assert result.shape == stereo_buffer.shape
            assert_no_nans(result, f"distortion_{character}")

    def test_distortion_adds_harmonics(self, mono_buffer):
        result = distortion(mono_buffer, drive=8.0, character="soft", sample_rate=SR)
        spec = np.abs(np.fft.rfft(result * np.hanning(len(result))))
        freqs = np.fft.rfftfreq(len(result), 1.0 / SR)
        third = spec[np.argmin(np.abs(freqs - 1320.0))]
        fundamental = spec[np.argmin(np.abs(freqs - 440.0))]
        assert third > fundamental * 0.01  # visible 3rd harmonic

    def test_bitcrush(self, stereo_buffer):
        result = bitcrush(stereo_buffer, bits=6, downsample=4, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "bitcrush")

    def test_bitcrush_quantizes(self, stereo_buffer):
        result = bitcrush(stereo_buffer, bits=3, downsample=1, wet=1.0, sample_rate=SR)
        assert len(np.unique(np.round(result[:, 0], 6))) <= 16

    def test_stutter_keeps_shape(self, stereo_buffer):
        result = stutter(stereo_buffer, sample_rate=SR, seed=42)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "stutter")

    def test_stutter_repeats_slices(self):
        ramp = np.linspace(0.0, 0.9, SR * 2, dtype=np.float32)
        buf = np.column_stack([ramp, ramp])
        result = stutter(buf, slice_ms=100, repeats=4, prob=1.0, sample_rate=SR, seed=1)
        assert not np.allclose(result, buf)

    def test_normalize_peak(self, stereo_buffer):
        quiet = stereo_buffer * 0.05
        result = normalize(quiet, target_db=-1.0)
        assert abs(float(np.max(np.abs(result))) - 10 ** (-1 / 20)) < 0.02

    def test_normalize_rms(self, stereo_buffer):
        result = normalize(stereo_buffer * 0.03, target_db=-14.0, mode="rms")
        rms = float(np.sqrt(np.mean(result**2)))
        assert abs(20 * np.log10(rms) - (-14.0)) < 1.5

    def test_bandpass(self, stereo_buffer):
        result = bandpass(stereo_buffer, center_hz=550.0, q=2.0, sample_rate=SR)
        assert result.shape == stereo_buffer.shape
        # the 550 Hz right channel should survive better than the 440 Hz left
        left_rms = float(np.sqrt(np.mean(result[:, 0] ** 2)))
        right_rms = float(np.sqrt(np.mean(result[:, 1] ** 2)))
        assert right_rms > left_rms

    def test_resonant_lowpass(self, stereo_buffer):
        result = lowpass(stereo_buffer, 1000.0, SR, q=4.0)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "resonant_lowpass")

    def test_spatial_near(self, stereo_buffer):
        result = spatial_near(stereo_buffer, SR)
        assert result.shape == stereo_buffer.shape
        assert_no_nans(result, "spatial_near")

    def test_spatial_wide_raises_side_energy(self, stereo_buffer):
        result = spatial_wide(stereo_buffer, SR)
        assert result.shape == stereo_buffer.shape
        side_in = float(np.mean(np.abs(stereo_buffer[:, 0] - stereo_buffer[:, 1])))
        side_out = float(np.mean(np.abs(result[:, 0] - result[:, 1])))
        assert side_out > side_in

    def test_reverb_tail_wraps_into_loop(self):
        """a decaying hit near the loop end must leave wash at the loop start."""
        buf = np.zeros((SR * 2, 2), dtype=np.float32)
        buf[SR * 2 - SR // 2] = 0.9  # hit half a second before the loop ends
        result = reverb(buf, wet=0.7, decay="long", sample_rate=SR, tail="wrap")
        head_rms = float(np.sqrt(np.mean(result[: SR // 4] ** 2)))
        assert head_rms > 1e-4
        assert result.shape == buf.shape


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
