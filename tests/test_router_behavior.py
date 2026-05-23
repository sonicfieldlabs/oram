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
    KillAudioAction,
    RecordAction,
)
from oram.config import OramConfig
from oram.types import OramSession


def _router(config=None):
    session = OramSession(id="test", scene="test")
    layers = LayerManager()
    session.layers = layers.layers
    engine = MockAudioEngine(session, layers)
    return ActionRouter(session, layers, engine, config=config), layers, engine


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
