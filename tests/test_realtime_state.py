"""tests for realtime audio state — playback snapshot, ring buffer, mixer performance."""

from __future__ import annotations

import time

import numpy as np
import pytest

from oram.audio.mixer import Mixer
from oram.audio.playback import BufferSwap, LayerSnapshot, PlaybackSnapshot, RingBuffer
from oram.types import Layer, LayerState


class TestRingBuffer:
    """pre-allocated ring buffer for recording."""

    def test_write_and_read(self):
        rb = RingBuffer(max_samples=1000, channels=2)
        data = np.random.randn(500, 2).astype(np.float32)
        written = rb.write(data)
        assert written == 500
        assert rb.samples_written == 500

        result = rb.read()
        assert result.shape == (500, 2)
        np.testing.assert_array_equal(result, data)

        # after read, buffer is reset
        assert rb.samples_written == 0

    def test_write_overflow_truncates(self):
        rb = RingBuffer(max_samples=100, channels=2)
        data = np.random.randn(200, 2).astype(np.float32)
        written = rb.write(data)
        assert written == 100
        assert rb.is_full

    def test_reset(self):
        rb = RingBuffer(max_samples=100, channels=2)
        rb.write(np.ones((50, 2), dtype=np.float32))
        rb.reset()
        assert rb.samples_written == 0
        assert not rb.is_full

    def test_multiple_writes(self):
        rb = RingBuffer(max_samples=1000, channels=2)
        for _ in range(3):
            rb.write(np.ones((100, 2), dtype=np.float32))
        assert rb.samples_written == 300
        result = rb.read()
        assert result.shape == (300, 2)

    def test_empty_read(self):
        rb = RingBuffer(max_samples=100, channels=2)
        result = rb.read()
        assert result.shape == (0, 2)


class TestPlaybackSnapshot:
    """immutable playback state."""

    def test_snapshot_is_frozen(self):
        snap = PlaybackSnapshot(
            layers=(),
            any_solo=False,
            revision=1,
        )
        assert snap.revision == 1
        with pytest.raises(AttributeError):
            snap.revision = 2

    def test_layer_snapshot_is_frozen(self):
        ls = LayerSnapshot(
            slot=0,
            buffer=np.zeros((100, 2), dtype=np.float32),
            playhead=0,
            volume=1.0,
            pan=0.0,
            muted=False,
            solo=False,
            is_empty=False,
            length_samples=100,
        )
        with pytest.raises(AttributeError):
            ls.volume = 0.5


class TestBufferSwap:
    """buffer swap message."""

    def test_buffer_swap_creation(self):
        buf = np.zeros((100, 2), dtype=np.float32)
        swap = BufferSwap(layer_slot=0, new_buffer=buf)
        assert swap.layer_slot == 0
        assert swap.new_playhead == 0
        assert swap.new_buffer.shape == (100, 2)


class TestMixerPerformance:
    """mixer vectorization performance."""

    def test_vectorized_pull_block(self):
        mixer = Mixer(sample_rate=48000, channels=2)

        # create a test layer
        layer = Layer(
            id="perf-test",
            name="perf_test",
            slot=0,
            sample_rate=48000,
            channels=2,
        )
        layer.buffer = np.random.randn(96000, 2).astype(np.float32)
        layer.duration_seconds = 2.0
        layer.playhead = 0
        layer.state = LayerState.ACTIVE

        # time the pull
        start = time.perf_counter()
        for _ in range(1000):
            mixer._pull_block(layer, 512)
        elapsed = time.perf_counter() - start

        # 1000 pulls of 512 samples should be well under 100ms
        assert elapsed < 0.5, f"mixer pull took {elapsed:.3f}s for 1000 blocks"

    def test_mixer_4_layers_within_budget(self):
        mixer = Mixer(sample_rate=48000, channels=2)

        layers = []
        for i in range(4):
            layer = Layer(
                id=f"bench-{i}",
                name=f"bench_{i}",
                slot=i,
                sample_rate=48000,
                channels=2,
            )
            layer.buffer = np.random.randn(96000, 2).astype(np.float32) * 0.5
            layer.duration_seconds = 2.0
            layer.playhead = 0
            layer.state = LayerState.ACTIVE
            layer.volume = 0.8
            layers.append(layer)

        start = time.perf_counter()
        for _ in range(100):
            mixer.mix_block(layers, 512)
        elapsed = time.perf_counter() - start

        # 100 mixes of 4 layers at 512 should be well under 100ms
        assert elapsed < 0.5, f"mixer 4-layer mix took {elapsed:.3f}s for 100 blocks"


class TestPanLaw:
    """constant-power pan law (§1.1)."""

    def test_constant_power_across_sweep(self):
        """L² + R² should be approximately constant for any pan value."""
        np.random.seed(0)
        mixer = Mixer(sample_rate=48000, channels=2)
        block = np.ones((512, 2), dtype=np.float32) * 0.5

        energies = []
        for pan_val in np.linspace(-1.0, 1.0, 21):
            b = block.copy()
            b = mixer._apply_pan(b, pan_val)
            # energy = sum of L² + R² per sample, averaged
            energy = float(np.mean(b[:, 0] ** 2 + b[:, 1] ** 2))
            energies.append(energy)

        # all energies should be within 1% of each other
        e_arr = np.array(energies)
        assert np.max(e_arr) - np.min(e_arr) < 0.01 * np.mean(e_arr), (
            f"pan energy varies too much: min={np.min(e_arr):.4f} max={np.max(e_arr):.4f}"
        )

    def test_full_left(self):
        mixer = Mixer(sample_rate=48000, channels=2)
        block = np.ones((10, 2), dtype=np.float32)
        result = mixer._apply_pan(block, -1.0)
        assert result[0, 1] == pytest.approx(0.0, abs=1e-6), "full left should silence right"
        assert result[0, 0] > 0.9, "full left should keep left loud"

    def test_full_right(self):
        mixer = Mixer(sample_rate=48000, channels=2)
        block = np.ones((10, 2), dtype=np.float32)
        result = mixer._apply_pan(block, 1.0)
        assert result[0, 0] == pytest.approx(0.0, abs=1e-6), "full right should silence left"
        assert result[0, 1] > 0.9, "full right should keep right loud"

    def test_center_equal_gains(self):
        mixer = Mixer(sample_rate=48000, channels=2)
        block = np.ones((10, 2), dtype=np.float32)
        result = mixer._apply_pan(block, 0.0)
        # at center, cos(π/4) = sin(π/4) ≈ 0.707 — both channels equal
        center_gain = float(np.cos(np.pi / 4.0))
        np.testing.assert_allclose(result[:, 0], center_gain, atol=1e-5)
        np.testing.assert_allclose(result[:, 1], center_gain, atol=1e-5)

