"""tests for Sprints 2-5: ElevenLabs pack expansion, Stable Audio,
local runner, auto-router health/history, and intent inference."""

from __future__ import annotations

import numpy as np
import pytest

from oram.engines.adapter import EngineSpec, GenerationRequest, GenerationResult
from oram.engines.capabilities import (
    AudioCapability,
    EngineMode,
    EngineProvider,
    SonicIntent,
)
from oram.engines.registry import EngineRegistry
from oram.engines.router import (
    EngineRouter,
    HealthStatus,
    RoutingDecision,
    infer_intent_from_analysis,
    resolve_intent,
    select_engine_v2,
)


# ── helpers ──

class _MockEngine:
    def __init__(self, spec: EngineSpec, available: bool = True, fail: bool = False):
        self.spec = spec
        self._available = available
        self._fail = fail
        self.call_count = 0

    def is_available(self) -> bool:
        return self._available

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if self._fail:
            raise RuntimeError("mock engine failure")
        self.call_count += 1
        sr = 48000
        samples = int(request.duration_seconds * sr)
        return GenerationResult(
            audio=np.zeros((samples, 2), dtype=np.float32),
            sample_rate=sr,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=request.prompt,
            duration_seconds=request.duration_seconds,
        )


# ── Sprint 2: ElevenLabs Pack Expansion ──

class TestElevenLabsPack:
    def test_voice_changer_spec(self):
        from oram.engines.elevenlabs import ElevenLabsVoiceChangerEngine
        engine = ElevenLabsVoiceChangerEngine(api_key="test-key")
        assert engine.spec.id == "elevenlabs-voice-changer"
        assert AudioCapability.SPEECH_TO_SPEECH in engine.spec.capabilities
        assert engine.spec.supports_audio_input is True
        assert engine.is_available()

    def test_voice_changer_requires_audio(self):
        from oram.engines.elevenlabs import ElevenLabsVoiceChangerEngine
        engine = ElevenLabsVoiceChangerEngine(api_key="test-key")
        req = GenerationRequest(prompt="test")
        with pytest.raises(ValueError, match="source_audio"):
            engine.generate(req)

    def test_voice_design_spec(self):
        from oram.engines.elevenlabs import ElevenLabsVoiceDesignEngine
        engine = ElevenLabsVoiceDesignEngine(api_key="test-key")
        assert engine.spec.id == "elevenlabs-voice-design"
        assert AudioCapability.VOICE_DESIGN in engine.spec.capabilities
        assert engine.spec.supports_audio_input is False

    def test_isolation_spec(self):
        from oram.engines.elevenlabs import ElevenLabsIsolationEngine
        engine = ElevenLabsIsolationEngine(api_key="test-key")
        assert engine.spec.id == "elevenlabs-isolation"
        assert AudioCapability.VOICE_ISOLATION in engine.spec.capabilities
        assert engine.spec.supports_audio_input is True

    def test_isolation_requires_audio(self):
        from oram.engines.elevenlabs import ElevenLabsIsolationEngine
        engine = ElevenLabsIsolationEngine(api_key="test-key")
        req = GenerationRequest(prompt="test")
        with pytest.raises(ValueError, match="source_audio"):
            engine.generate(req)

    def test_from_config_registers_7_elevenlabs(self):
        from oram.config import OramConfig
        config = OramConfig()
        config.elevenlabs_api_key = "test-key-12345"
        reg = EngineRegistry.from_config(config)
        el_engines = reg.get_by_provider(EngineProvider.ELEVENLABS)
        assert len(el_engines) == 7
        ids = {e.spec.id for e in el_engines}
        assert "elevenlabs-voice-changer" in ids
        assert "elevenlabs-voice-design" in ids
        assert "elevenlabs-isolation" in ids

    def test_voice_design_intent_routing(self):
        """SonicIntent.VOICE should NOT route to voice-design (it's a meta-engine)."""
        from oram.engines.elevenlabs import ElevenLabsVoiceDesignEngine
        spec = ElevenLabsVoiceDesignEngine(api_key="test").spec
        assert not spec.supports_intent(SonicIntent.VOICE)
        assert not spec.supports_intent(SonicIntent.SOUND_EFFECT)


# ── Sprint 3: Stable Audio ──

class TestStableAudio:
    def test_stability_stable_audio_spec(self):
        from oram.engines.stable_audio import StabilityStableAudioEngine
        engine = StabilityStableAudioEngine(api_key="test-stability-key")
        assert engine.spec.id == "stability-stable-audio-2"
        assert engine.spec.provider == EngineProvider.STABILITY
        assert AudioCapability.TEXT_TO_MUSIC in engine.spec.capabilities
        assert AudioCapability.TEXT_TO_SOUND_EFFECT in engine.spec.capabilities
        assert engine.spec.supports_seed is True
        assert engine.is_available()

    def test_stable_audio_spec(self):
        from oram.engines.stable_audio import StableAudioEngine
        engine = StableAudioEngine(api_key="test-fal-key")
        assert engine.spec.id == "stable-audio-25"
        assert engine.spec.provider == EngineProvider.FAL
        assert AudioCapability.TEXT_TO_MUSIC in engine.spec.capabilities
        assert AudioCapability.TEXT_TO_SOUND_EFFECT in engine.spec.capabilities
        assert AudioCapability.AUDIO_TO_AUDIO in engine.spec.capabilities
        assert engine.spec.supports_seed is True
        assert engine.spec.max_duration_seconds == 47.0

    def test_stable_audio_availability(self):
        from oram.engines.stable_audio import StableAudioEngine
        assert not StableAudioEngine(api_key="").is_available()
        assert StableAudioEngine(api_key="fal-key-123").is_available()

    def test_stable_audio_registered_from_config(self):
        from oram.config import OramConfig
        config = OramConfig()
        config.fal_key = "test-fal-key"
        reg = EngineRegistry.from_config(config)
        engine = reg.get("stable-audio-25")
        assert engine is not None
        assert engine.spec.provider == EngineProvider.FAL

    def test_stability_stable_audio_registered_from_config(self):
        from oram.config import OramConfig
        config = OramConfig()
        config.stability_api_key = "test-stability-key"
        reg = EngineRegistry.from_config(config)
        engine = reg.get("stability-stable-audio-2")
        assert engine is not None
        assert engine.spec.provider == EngineProvider.STABILITY

    def test_stable_audio_intent_support(self):
        from oram.engines.stable_audio import StableAudioEngine
        spec = StableAudioEngine(api_key="test").spec
        assert spec.supports_intent(SonicIntent.MUSIC)
        assert spec.supports_intent(SonicIntent.SOUND_EFFECT)
        assert spec.supports_intent(SonicIntent.TEXTURE)  # via text_to_sound_effect


# ── Sprint 4: Local Runner ──

class TestLocalRunner:
    def test_local_mock_spec(self):
        from oram.engines.local_runner import LocalMockEngine
        engine = LocalMockEngine()
        assert engine.spec.id == "local-mock"
        assert engine.spec.provider == EngineProvider.LOCAL
        assert engine.spec.mode == EngineMode.LOCAL
        assert engine.spec.requires_api_key is False
        assert engine.spec.cost_per_second == 0.0

    def test_local_mock_always_available(self):
        from oram.engines.local_runner import LocalMockEngine
        assert LocalMockEngine().is_available()

    def test_local_mock_generates_audio(self):
        from oram.engines.local_runner import LocalMockEngine
        engine = LocalMockEngine(sample_rate=48000)
        req = GenerationRequest(prompt="rain ambient", duration_seconds=2.0)
        result = engine.generate(req)
        assert result.audio.shape[1] == 2
        assert result.sample_rate == 48000
        assert result.cost_credits == 0.0
        assert result.engine_id == "local-mock"

    def test_local_mock_registered_from_config(self):
        from oram.config import OramConfig
        config = OramConfig()
        reg = EngineRegistry.from_config(config)
        engine = reg.get("local-mock")
        assert engine is not None
        assert engine.is_available()

    def test_sidecar_spec_tangoflux(self):
        from oram.engines.local_runner import LocalSidecarEngine
        engine = LocalSidecarEngine(model_id="tangoflux")
        assert engine.spec.id == "local-tangoflux"
        assert AudioCapability.TEXT_TO_SOUND_EFFECT in engine.spec.capabilities
        assert engine.spec.requires_api_key is False

    def test_sidecar_spec_kokoro(self):
        from oram.engines.local_runner import LocalSidecarEngine
        engine = LocalSidecarEngine(model_id="kokoro")
        assert engine.spec.id == "local-kokoro"
        assert AudioCapability.TEXT_TO_SPEECH in engine.spec.capabilities

    def test_sidecar_spec_whisper(self):
        from oram.engines.local_runner import LocalSidecarEngine
        engine = LocalSidecarEngine(model_id="whisper")
        assert engine.spec.id == "local-whisper"
        assert AudioCapability.SPEECH_TO_TEXT in engine.spec.capabilities
        assert engine.spec.supports_audio_input is True

    def test_sidecar_unavailable_by_default(self):
        """sidecar should be unavailable when no sidecar is running."""
        from oram.engines.local_runner import LocalSidecarEngine
        engine = LocalSidecarEngine(model_id="tangoflux", host="127.0.0.1", port=19999)
        assert not engine.is_available()


# ── Sprint 5: Health Tracking ──

class TestHealthTracking:
    def test_health_status_reliability(self):
        h = HealthStatus(engine_id="test")
        assert h.reliability == 1.0  # no data = 100%

        h.success_count = 9
        h.error_count = 1
        assert h.reliability == 0.9

    def test_health_tracked_on_execute(self):
        sfx = _MockEngine(
            EngineSpec(
                id="test-sfx", provider=EngineProvider.ELEVENLABS,
                label="Test", mode=EngineMode.CLOUD,
                capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT],
            )
        )
        reg = EngineRegistry()
        reg.register(sfx)
        router = EngineRouter(reg)

        req = GenerationRequest(prompt="test", intent=SonicIntent.SOUND_EFFECT)
        router.execute(req)

        health = router.get_health()
        assert "test-sfx" in health
        assert health["test-sfx"].success_count == 1
        assert health["test-sfx"].last_latency_ms >= 0

    def test_health_tracked_on_error(self):
        sfx = _MockEngine(
            EngineSpec(
                id="test-sfx", provider=EngineProvider.ELEVENLABS,
                label="Test", mode=EngineMode.CLOUD,
                capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT],
            ),
            fail=True,
        )
        reg = EngineRegistry()
        reg.register(sfx)
        router = EngineRouter(reg)

        req = GenerationRequest(prompt="test", intent=SonicIntent.SOUND_EFFECT)
        with pytest.raises(RuntimeError, match="mock engine failure"):
            router.execute(req)

        health = router.get_health()
        assert health["test-sfx"].error_count == 1
        assert health["test-sfx"].success_count == 0


# ── Sprint 5: Routing History ──

class TestRoutingHistory:
    def test_history_empty_initially(self):
        reg = EngineRegistry()
        router = EngineRouter(reg)
        assert router.get_history() == []

    def test_history_capped(self):
        """history should be capped at HISTORY_MAX entries."""
        reg = EngineRegistry()
        router = EngineRouter(reg)
        for i in range(60):
            router._record_decision(RoutingDecision(
                engine_id=f"e-{i}", provider="test", reason="test",
                confidence=0.5,
            ))
        assert len(router.get_history(limit=100)) == 50  # HISTORY_MAX

    def test_registry_property(self):
        reg = EngineRegistry()
        router = EngineRouter(reg)
        assert router.registry is reg


# ── Sprint 5: Intent Inference ──

class TestIntentInference:
    def test_speech_detected(self):
        intent = infer_intent_from_analysis({"contains_speech": True})
        assert intent == SonicIntent.VOICE

    def test_voice_detected(self):
        intent = infer_intent_from_analysis({"contains_voice": True})
        assert intent == SonicIntent.VOICE

    def test_music_by_pitch(self):
        intent = infer_intent_from_analysis({"pitch_confidence": 0.8})
        assert intent == SonicIntent.MUSIC

    def test_music_by_rhythm(self):
        intent = infer_intent_from_analysis({"rhythmic_regularity": 0.9})
        assert intent == SonicIntent.MUSIC

    def test_texture_tonal_arrhythmic(self):
        intent = infer_intent_from_analysis({
            "pitch_confidence": 0.5,
            "rhythmic_regularity": 0.1,
        })
        assert intent == SonicIntent.TEXTURE

    def test_sfx_default(self):
        intent = infer_intent_from_analysis({
            "is_noisy": True,
            "is_gestural": True,
        })
        assert intent == SonicIntent.SOUND_EFFECT

    def test_sfx_empty_analysis(self):
        intent = infer_intent_from_analysis({})
        assert intent == SonicIntent.SOUND_EFFECT


# ── Sprint 5: Legacy Bridge ──

class TestLegacyBridge:
    def test_select_engine_v2_explicit_mode(self):
        decision = select_engine_v2(analysis={}, user_mode="sfx")
        assert decision.intent == "sound_effect" or decision.engine_id == "sound_effect"

    def test_select_engine_v2_auto_with_speech(self):
        decision = select_engine_v2(
            analysis={"contains_speech": True},
            user_mode="auto",
        )
        assert decision.intent == "voice" or decision.engine_id == "voice"

    def test_select_engine_v2_with_router(self):
        sfx = _MockEngine(
            EngineSpec(
                id="test-sfx", provider=EngineProvider.ELEVENLABS,
                label="Test", mode=EngineMode.CLOUD,
                capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT],
            )
        )
        reg = EngineRegistry()
        reg.register(sfx)
        router = EngineRouter(reg)

        decision = select_engine_v2(
            analysis={"is_noisy": True},
            user_mode="auto",
            router=router,
        )
        assert decision.engine_id == "test-sfx"

    def test_select_engine_v2_voice_override_with_router(self):
        tts = _MockEngine(
            EngineSpec(
                id="test-tts", provider=EngineProvider.ELEVENLABS,
                label="Test TTS", mode=EngineMode.CLOUD,
                capabilities=[AudioCapability.TEXT_TO_SPEECH],
            )
        )
        reg = EngineRegistry()
        reg.register(tts)
        router = EngineRouter(reg)

        decision = select_engine_v2(
            analysis={},
            user_mode="voice",
            router=router,
        )
        assert decision.engine_id == "test-tts"
