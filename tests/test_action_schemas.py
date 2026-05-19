"""tests for action schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from oram.command.schemas import (
    ApplyEffectAction,
    EffectParameters,
    GenerateLayerAction,
    RecordAction,
    RemoveEffectAction,
    SelectLayerAction,
    SetVolumeAction,
)


class TestSchemaValidation:
    def test_select_layer_range(self):
        """layer target must be 1-8."""
        SelectLayerAction(target=1)
        SelectLayerAction(target=4)
        with pytest.raises(ValidationError):
            SelectLayerAction(target=0)
        with pytest.raises(ValidationError):
            SelectLayerAction(target=9)

    def test_volume_range(self):
        """volume must be 0.0 to 2.0."""
        SetVolumeAction(volume=0.0)
        SetVolumeAction(volume=2.0)
        with pytest.raises(ValidationError):
            SetVolumeAction(volume=-0.1)
        with pytest.raises(ValidationError):
            SetVolumeAction(volume=2.1)

    def test_effect_speed_range(self):
        """speed must be 0.25 to 4.0."""
        p = EffectParameters(speed=0.5)
        assert p.speed == 0.5
        with pytest.raises(ValidationError):
            EffectParameters(speed=0.1)
        with pytest.raises(ValidationError):
            EffectParameters(speed=5.0)

    def test_effect_semitones_range(self):
        """semitones must be -12 to 12."""
        p = EffectParameters(semitones=-12.0)
        assert p.semitones == -12.0
        p = EffectParameters(semitones=12.0)
        assert p.semitones == 12.0
        with pytest.raises(ValidationError):
            EffectParameters(semitones=-13.0)
        with pytest.raises(ValidationError):
            EffectParameters(semitones=13.0)

    def test_effect_density_range(self):
        """density must be 0.0 to 1.0."""
        p = EffectParameters(density=0.5)
        assert p.density == 0.5
        with pytest.raises(ValidationError):
            EffectParameters(density=-0.1)
        with pytest.raises(ValidationError):
            EffectParameters(density=1.1)

    def test_generate_requires_prompt(self):
        """generate_layer must have a prompt."""
        g = GenerateLayerAction(prompt="distant rain")
        assert g.prompt == "distant rain"
        with pytest.raises(ValidationError):
            GenerateLayerAction()  # type: ignore

    def test_generate_mix_level_range(self):
        """mix_level must be 0.0 to 1.0."""
        g = GenerateLayerAction(prompt="test", mix_level=0.5)
        assert g.mix_level == 0.5
        with pytest.raises(ValidationError):
            GenerateLayerAction(prompt="test", mix_level=1.5)

    def test_record_defaults(self):
        """record action should have sensible defaults."""
        r = RecordAction()
        assert r.target == "selected"
        assert r.duration is None
        assert r.overdub is False

    def test_apply_effect_serialization(self):
        """action should round-trip through JSON."""
        a = ApplyEffectAction(
            target=1,
            effect="granular",
            parameters=EffectParameters(density=0.35, grain_size_ms=120, jitter=0.2),
        )
        data = a.model_dump()
        assert data["action"] == "apply_effect"
        assert data["effect"] == "granular"
        assert data["parameters"]["density"] == 0.35

        # round-trip
        restored = ApplyEffectAction.model_validate(data)
        assert restored.parameters.density == 0.35

    def test_invalid_effect_rejected(self):
        """unknown effect names should fail schema validation."""
        with pytest.raises(ValidationError):
            ApplyEffectAction(effect="explode")
        with pytest.raises(ValidationError):
            RemoveEffectAction(effect="explode")
