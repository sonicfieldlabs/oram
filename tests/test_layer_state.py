"""tests for layer state transitions."""

from __future__ import annotations

import numpy as np
import pytest

from oram.audio.engine import MockAudioEngine
from oram.audio.layer import LayerManager
from oram.types import LayerState, OramSession


class TestLayerState:
    def test_initial_state(self):
        mgr = LayerManager()
        assert len(mgr.layers) == 4
        for layer in mgr.layers:
            assert layer.state == LayerState.EMPTY
            assert layer.is_empty

    def test_select_layer(self):
        mgr = LayerManager()
        mgr.select(2)
        assert mgr.selected == 1
        assert mgr.selected_layer.slot == 1

    def test_invalid_layer_target_raises(self):
        mgr = LayerManager()
        with pytest.raises(ValueError, match="invalid layer target"):
            mgr.get_layer(99)

    def test_assign_buffer(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        buf = np.random.randn(48000, 2).astype(np.float32)
        mgr.assign_buffer(layer, buf)
        assert layer.state == LayerState.ACTIVE
        assert not layer.is_empty
        assert layer.duration_seconds == 1.0

    def test_assign_mono_buffer(self):
        """mono buffers should be duplicated to stereo."""
        mgr = LayerManager()
        layer = mgr.layers[0]
        mono = np.random.randn(48000).astype(np.float32)
        mgr.assign_buffer(layer, mono)
        assert layer.buffer.shape == (48000, 2)

    def test_mute_toggle(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        buf = np.random.randn(48000, 2).astype(np.float32)
        mgr.assign_buffer(layer, buf)

        mgr.mute(layer)
        assert layer.muted
        assert layer.state == LayerState.MUTED

        mgr.mute(layer)
        assert not layer.muted
        assert layer.state == LayerState.ACTIVE

    def test_mute_empty_layer(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        mgr.mute(layer)  # should do nothing
        assert layer.state == LayerState.EMPTY

    def test_solo(self):
        mgr = LayerManager()
        for i in range(2):
            buf = np.random.randn(48000, 2).astype(np.float32)
            mgr.assign_buffer(mgr.layers[i], buf)

        mgr.solo(mgr.layers[0])
        assert mgr.layers[0].solo
        assert not mgr.layers[1].solo

        # solo another unsolos first
        mgr.solo(mgr.layers[1])
        assert not mgr.layers[0].solo
        assert mgr.layers[1].solo

    def test_clear_and_undo(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        buf = np.random.randn(48000, 2).astype(np.float32)
        mgr.assign_buffer(layer, buf)

        mgr.clear(layer)
        assert layer.state == LayerState.EMPTY
        assert layer.is_empty

        # undo
        result = mgr.undo_clear(layer)
        assert result
        assert layer.state == LayerState.ACTIVE
        assert not layer.is_empty

    def test_overdub(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        buf = np.zeros((48000, 2), dtype=np.float32)
        mgr.assign_buffer(layer, buf)

        overdub = np.ones((24000, 2), dtype=np.float32) * 0.5
        mgr.overdub(layer, overdub)

        # buffer should have changed
        assert np.any(layer.buffer != 0)

    def test_get_active_layers(self):
        mgr = LayerManager()
        # empty: no active layers
        assert mgr.get_active_layers() == []

        # add a buffer
        buf = np.random.randn(48000, 2).astype(np.float32)
        mgr.assign_buffer(mgr.layers[0], buf)
        active = mgr.get_active_layers()
        assert len(active) == 1

        # mute it
        mgr.mute(mgr.layers[0])
        assert mgr.get_active_layers() == []

    def test_solo_overrides_mute(self):
        mgr = LayerManager()
        for i in range(3):
            buf = np.random.randn(48000, 2).astype(np.float32)
            mgr.assign_buffer(mgr.layers[i], buf)

        mgr.mute(mgr.layers[0])
        mgr.solo(mgr.layers[1])

        active = mgr.get_active_layers()
        assert len(active) == 1
        assert active[0].slot == 1  # layer_2 (0-indexed slot=1)

    def test_stop_recording_without_samples_restores_empty_state(self):
        mgr = LayerManager()
        session = OramSession(id="test", scene="test")
        session.layers = mgr.layers
        engine = MockAudioEngine(session, mgr)

        engine.start_recording(target=1)
        result = engine.stop_recording()

        assert result is None
        assert mgr.layers[0].state == LayerState.EMPTY

    def test_waveform_revision_increments_on_assign(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        assert layer.waveform_revision == 0
        buf = np.random.randn(48000, 2).astype(np.float32)
        mgr.assign_buffer(layer, buf)
        assert layer.waveform_revision == 1
        mgr.assign_buffer(layer, buf)
        assert layer.waveform_revision == 2

    def test_waveform_revision_increments_on_clear(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        buf = np.random.randn(48000, 2).astype(np.float32)
        mgr.assign_buffer(layer, buf)
        rev_before = layer.waveform_revision
        mgr.clear(layer)
        assert layer.waveform_revision == rev_before + 1

    def test_waveform_revision_increments_on_overdub(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        buf = np.zeros((48000, 2), dtype=np.float32)
        mgr.assign_buffer(layer, buf)
        rev_before = layer.waveform_revision
        overdub = np.ones((24000, 2), dtype=np.float32) * 0.5
        mgr.overdub(layer, overdub)
        assert layer.waveform_revision == rev_before + 1

    def test_set_loop_region_stores_offsets(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        buf = np.random.randn(48000, 2).astype(np.float32)
        mgr.assign_buffer(layer, buf)
        mgr.set_loop_region(layer, 4800, 24000)
        assert layer.looper.start_offset == 4800
        assert layer.looper.end_offset == 24000
        assert layer.looper.enabled is True

    def test_clear_resets_loop_region(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        buf = np.random.randn(48000, 2).astype(np.float32)
        mgr.assign_buffer(layer, buf)
        mgr.set_loop_region(layer, 4800, 24000)
        mgr.clear(layer)
        assert layer.looper.start_offset == 0
        assert layer.looper.end_offset == 0
        assert layer.looper.enabled is False

    def test_assign_buffer_resets_stale_loop_region(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        mgr.assign_buffer(layer, np.random.randn(48000, 2).astype(np.float32))
        mgr.set_loop_region(layer, 12000, 36000)

        mgr.assign_buffer(layer, np.random.randn(2400, 2).astype(np.float32))

        assert layer.looper.start_offset == 0
        assert layer.looper.end_offset == 0
        assert layer.looper.enabled is False

    def test_swap_buffer_clamps_loop_region(self):
        mgr = LayerManager()
        layer = mgr.layers[0]
        mgr.assign_buffer(layer, np.random.randn(48000, 2).astype(np.float32))
        mgr.set_loop_region(layer, 12000, 36000)

        mgr.swap_buffer(layer, np.random.randn(2400, 2).astype(np.float32))

        assert layer.looper.start_offset == 0
        assert layer.looper.end_offset == 2400
        assert layer.looper.enabled is True
