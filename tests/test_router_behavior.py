"""tests for action router behavior that affects performance safety."""

from __future__ import annotations

import numpy as np

from oram.audio.engine import MockAudioEngine
from oram.audio.layer import LayerManager
from oram.command.router import ActionRouter
from oram.command.schemas import (
    ApplyEffectAction,
    ClearLayerAction,
    EffectParameters,
    GenerateLayerAction,
    KillAudioAction,
    RecordAction,
)
from oram.config import OramConfig
from oram.types import OramSession


def _router(config=None, on_before_layer_mutation=None):
    session = OramSession(id="test", scene="test")
    layers = LayerManager()
    session.layers = layers.layers
    engine = MockAudioEngine(session, layers)
    return ActionRouter(
        session,
        layers,
        engine,
        config=config,
        on_before_layer_mutation=on_before_layer_mutation,
    ), layers, engine


def test_record_action_respects_target_layer():
    router, layers, engine = _router()

    router.route(RecordAction(target=2, duration=1.0))

    assert layers.selected == 1
    assert engine._record_target == 2


def test_clear_requires_repeat_confirmation():
    router, layers, _engine = _router()
    layers.assign_buffer(layers.layers[0], np.ones((100, 2), dtype=np.float32))

    first = router.route(ClearLayerAction(target=1))
    assert "confirm clear layer 1" in first
    assert not layers.layers[0].is_empty

    second = router.route(ClearLayerAction(target=1))
    assert second == "layer 1 cleared"
    assert layers.layers[0].is_empty


def test_invalid_clear_target_does_not_clear_selected_layer():
    router, layers, _engine = _router()
    layers.assign_buffer(layers.layers[0], np.ones((100, 2), dtype=np.float32))

    result = router.route(ClearLayerAction(target=99, confirmed=True))

    assert "invalid layer target" in result
    assert not layers.layers[0].is_empty


def test_record_duration_is_clamped_before_engine_allocation():
    cfg = OramConfig(max_loop_seconds=1.0)
    router, _layers, engine = _router(config=cfg)

    router.route(RecordAction(target=1, duration=999.0))

    assert engine._record_max_samples == engine.sample_rate


def test_kill_audio_stops_capture_and_mutes_layers():
    router, layers, engine = _router()
    layers.assign_buffer(layers.layers[0], np.ones((100, 2), dtype=np.float32))
    layers.assign_buffer(layers.layers[1], np.ones((100, 2), dtype=np.float32))
    router.route(RecordAction(target=1, duration=1.0))
    engine.start_command_capture()
    epoch_before = router.audio_kill_epoch

    result = router.route(KillAudioAction())

    assert result == "killed all audio"
    assert router.audio_kill_epoch == epoch_before + 1
    assert engine._recording is False
    assert engine._command_capture is False
    assert engine.get_input_level() == 0.0
    assert engine.get_output_level() == 0.0
    assert [layer.muted for layer in layers.layers[:2]] == [True, True]
    assert [layer.playhead for layer in layers.layers[:2]] == [0, 0]


def test_make_everything_softer_reduces_all_active_layer_volumes():
    router, layers, _engine = _router()
    layers.assign_buffer(layers.layers[0], np.ones((100, 2), dtype=np.float32))
    layers.assign_buffer(layers.layers[1], np.ones((100, 2), dtype=np.float32))

    result = router.route(
        ApplyEffectAction(
            target="all",
            effect="fade_out",
            parameters=EffectParameters(fade_seconds=0.0),
        )
    )

    assert result == "all layers softer"
    assert layers.layers[0].volume == 0.8
    assert layers.layers[1].volume == 0.8


def test_apply_speed_fx_is_realtime_playback_state_not_buffer_resize():
    router, layers, _engine = _router()
    layer = layers.layers[0]
    layers.assign_buffer(layer, np.ones((1000, 2), dtype=np.float32) * 0.2)
    layer.playhead = 500

    router._apply_dsp(
        layer,
        ApplyEffectAction(
            target=1,
            effect="speed",
            parameters=EffectParameters(speed=2.0),
        ),
    )

    assert layer.buffer.shape == (1000, 2)
    assert layer.playhead == 500
    assert layer.speed == 2.0
    assert "speed" in layer.effects_applied
    assert not np.any(np.isnan(layer.buffer))
    assert not np.any(np.isinf(layer.buffer))


def test_generation_worker_captures_target_layer_history_before_assignment():
    calls = []
    router, layers, _engine = _router(
        on_before_layer_mutation=lambda label, layer: calls.append(
            (label, layer.slot, layer.is_empty, layer.buffer.copy())
        )
    )
    router._call_engine = lambda *args, **kwargs: np.ones((128, 2), dtype=np.float32) * 0.1

    router._generate_worker(
        GenerateLayerAction(prompt="quiet tone", duration=0.5),
        router.audio_kill_epoch,
    )

    assert calls
    label, slot, was_empty, buffer_before = calls[0]
    assert label == "generate"
    assert slot == 0
    assert was_empty is True
    assert buffer_before.shape == (0, 2)
    assert not layers.layers[0].is_empty


def test_generation_source_snapshot_prefers_bounded_loop_region_copy():
    session = OramSession(id="test", scene="test", sample_rate=1000)
    layers = LayerManager(sample_rate=1000)
    session.layers = layers.layers
    engine = MockAudioEngine(session, layers, sample_rate=1000)
    router = ActionRouter(session, layers, engine)
    source = layers.layers[0]
    audio = np.arange(4000, dtype=np.float32).reshape(2000, 2)
    layers.assign_buffer(source, audio)
    layers.set_loop_region(source, 500, 1500, enabled=True)
    expected_first = source.buffer[500].copy()

    snapshot = router._snapshot_layer_for_generation(source, max_seconds=0.25)

    assert snapshot.buffer.shape == (250, 2)
    np.testing.assert_array_equal(snapshot.buffer[0], expected_first)
    source.buffer[500] = 999
    assert not np.array_equal(snapshot.buffer[0], source.buffer[500])
