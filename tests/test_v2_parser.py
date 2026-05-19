"""tests for v2 schema validation — extra fields, layer limits, validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from oram.command.schemas import (
    EffectParameters,
    GenerateFromAction,
    GenerateLayerAction,
    ListenAction,
    RecordAction,
    SelectLayerAction,
)


class TestSelectLayerLimits:
    """SelectLayerAction.target must be within MAX_LAYERS."""

    def test_valid_layers(self):
        for n in range(1, 5):
            action = SelectLayerAction(target=n)
            assert action.target == n

    def test_layer_5_rejected(self):
        with pytest.raises(ValidationError):
            SelectLayerAction(target=5)

    def test_layer_8_rejected(self):
        with pytest.raises(ValidationError):
            SelectLayerAction(target=8)

    def test_layer_0_rejected(self):
        with pytest.raises(ValidationError):
            SelectLayerAction(target=0)


class TestExtraFieldsRejected:
    """extra=forbid should reject unknown fields from LLM/API output."""

    def test_select_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            SelectLayerAction(target=1, bogus="field")

    def test_record_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            RecordAction(target=1, fake_param=42)

    def test_listen_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            ListenAction(target=1, route="hybrid", unknown="x")


class TestDurationValidators:
    """duration validators on record and generate actions."""

    def test_record_negative_duration_rejected(self):
        with pytest.raises(ValidationError):
            RecordAction(duration=-1.0)

    def test_record_zero_duration_rejected(self):
        with pytest.raises(ValidationError):
            RecordAction(duration=0.0)

    def test_record_none_duration_valid(self):
        action = RecordAction(duration=None)
        assert action.duration is None

    def test_generate_duration_min(self):
        with pytest.raises(ValidationError):
            GenerateLayerAction(prompt="test", duration=0.1)

    def test_generate_duration_max(self):
        with pytest.raises(ValidationError):
            GenerateLayerAction(prompt="test", duration=1000.0)

    def test_generate_duration_valid(self):
        action = GenerateLayerAction(prompt="test", duration=10.0)
        assert action.duration == 10.0


class TestEngineValidators:
    """engine field validation."""

    def test_valid_engines(self):
        for eng in ("auto", "sfx", "voice", "music"):
            action = GenerateFromAction(engine=eng)
            assert action.engine == eng

    def test_invalid_engine_rejected(self):
        with pytest.raises(ValidationError):
            GenerateFromAction(engine="dalle")

    def test_generate_layer_invalid_engine(self):
        with pytest.raises(ValidationError):
            GenerateLayerAction(prompt="test", engine="invalid")


class TestRouteValidators:
    """route field validation."""

    def test_valid_routes(self):
        for r in ("technical", "descriptive", "speculative", "hybrid"):
            action = ListenAction(route=r)
            assert action.route == r

    def test_invalid_route_rejected(self):
        with pytest.raises(ValidationError):
            ListenAction(route="random")


class TestDecayValidator:
    """EffectParameters.decay validation."""

    def test_valid_decays(self):
        for d in ("short", "medium", "long"):
            params = EffectParameters(decay=d)
            assert params.decay == d

    def test_invalid_decay_rejected(self):
        with pytest.raises(ValidationError):
            EffectParameters(decay="infinite")

    def test_none_decay_valid(self):
        params = EffectParameters(decay=None)
        assert params.decay is None
