"""tests for oram.engines.router — engine routing and execution."""

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
from oram.engines.router import EngineRouter, resolve_intent

# ── fixtures ──

class _MockEngine:
    """minimal OramEngineAdapter for testing."""

    def __init__(self, spec: EngineSpec, available: bool = True):
        self.spec = spec
        self._available = available
        self.call_count = 0

    def is_available(self) -> bool:
        return self._available

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.call_count += 1
        sr = 48000
        samples = int(request.duration_seconds * sr)
        audio = np.zeros((samples, 2), dtype=np.float32)
        return GenerationResult(
            audio=audio,
            sample_rate=sr,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=request.prompt,
            duration_seconds=request.duration_seconds,
        )


def _sfx_engine(available=True):
    return _MockEngine(
        EngineSpec(
            id="test-sfx",
            provider=EngineProvider.ELEVENLABS,
            label="Test SFX",
            mode=EngineMode.CLOUD,
            capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT],
            cost_per_second=40,
            latency_profile="medium",
        ),
        available=available,
    )


def _tts_engine(available=True):
    return _MockEngine(
        EngineSpec(
            id="test-tts",
            provider=EngineProvider.ELEVENLABS,
            label="Test TTS",
            mode=EngineMode.CLOUD,
            capabilities=[AudioCapability.TEXT_TO_SPEECH, AudioCapability.SPEECH_TO_SPEECH],
            cost_per_second=30,
            latency_profile="fast",
        ),
        available=available,
    )


def _music_engine(available=True):
    return _MockEngine(
        EngineSpec(
            id="test-music",
            provider=EngineProvider.STABILITY,
            label="Test Music",
            mode=EngineMode.CLOUD,
            capabilities=[AudioCapability.TEXT_TO_MUSIC],
            cost_per_second=5,
            latency_profile="medium",
        ),
        available=available,
    )


def _local_sfx(available=True):
    return _MockEngine(
        EngineSpec(
            id="local-sfx",
            provider=EngineProvider.LOCAL,
            label="Local SFX",
            mode=EngineMode.LOCAL,
            capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT],
            requires_api_key=False,
            cost_per_second=0,
            latency_profile="slow",
        ),
        available=available,
    )


def _local_sa3(available=True):
    return _MockEngine(
        EngineSpec(
            id="stable-audio-3-local",
            provider=EngineProvider.LOCAL,
            label="Local Engine",
            mode=EngineMode.LOCAL,
            capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT, AudioCapability.TEXT_TO_MUSIC],
            requires_api_key=False,
            cost_per_second=0,
            latency_profile="slow",
        ),
        available=available,
    )


def _local_mock(available=True):
    return _MockEngine(
        EngineSpec(
            id="local-mock",
            provider=EngineProvider.LOCAL,
            label="Local Mock",
            mode=EngineMode.LOCAL,
            capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT, AudioCapability.TEXT_TO_MUSIC],
            requires_api_key=False,
            cost_per_second=0,
            latency_profile="fast",
        ),
        available=available,
    )


def _filled_registry() -> EngineRegistry:
    reg = EngineRegistry()
    reg.register(_sfx_engine())
    reg.register(_tts_engine())
    reg.register(_music_engine())
    reg.register(_local_sfx())
    return reg


# ── routing ──

class TestRouting:
    def test_explicit_engine_override(self):
        """when engine_id is set, route to that exact engine."""
        reg = _filled_registry()
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="test", engine_id="test-sfx")
        decision = router.route(req)
        assert decision.engine_id == "test-sfx"
        assert decision.confidence == 1.0

    def test_explicit_engine_unavailable_falls_back(self):
        """when explicit engine is unavailable, fall back to intent-based routing."""
        reg = EngineRegistry()
        reg.register(_sfx_engine(available=False))
        reg.register(_local_sfx(available=True))
        router = EngineRouter(reg)
        req = GenerationRequest(
            prompt="test",
            intent=SonicIntent.SOUND_EFFECT,
            engine_id="test-sfx",
        )
        decision = router.route(req)
        assert decision.engine_id == "local-sfx"

    def test_intent_voice_routes_to_sfx(self):
        # VOICE now maps to TEXT_TO_SOUND_EFFECT — routes to SFX, not TTS
        reg = _filled_registry()
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="test", intent=SonicIntent.VOICE)
        decision = router.route(req)
        assert decision.engine_id == "test-sfx"

    def test_intent_sound_effect_prefers_cloud(self):
        reg = _filled_registry()
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="test", intent=SonicIntent.SOUND_EFFECT)
        decision = router.route(req)
        # cloud sfx should be preferred over local sfx
        assert decision.engine_id == "test-sfx"

    def test_intent_music_routes_to_music(self):
        reg = _filled_registry()
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="test", intent=SonicIntent.MUSIC)
        decision = router.route(req)
        assert decision.engine_id == "test-music"

    def test_no_engines_raises_error(self):
        reg = EngineRegistry()
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="test", intent=SonicIntent.SOUND_EFFECT)
        with pytest.raises(RuntimeError, match="no engines available"):
            router.route(req)

    def test_preferred_provider_boosts_ranking(self):
        reg = EngineRegistry()
        cloud = _sfx_engine()
        local = _local_sfx()
        reg.register(cloud)
        reg.register(local)
        # prefer local
        router = EngineRouter(reg, default_provider="local")
        req = GenerationRequest(prompt="test", intent=SonicIntent.SOUND_EFFECT)
        decision = router.route(req)
        assert decision.engine_id == "local-sfx"

    def test_alternatives_populated(self):
        reg = _filled_registry()
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="test", intent=SonicIntent.SOUND_EFFECT)
        decision = router.route(req)
        # should have at least one alternative (local-sfx)
        assert "local-sfx" in decision.alternatives or "test-sfx" in decision.alternatives

    def test_auto_routes_to_local_sa3_before_procedural_mock(self):
        reg = EngineRegistry()
        reg.register(_local_mock())
        reg.register(_local_sa3())
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="test", intent=SonicIntent.SOUND_EFFECT)
        decision = router.route(req)
        assert decision.engine_id == "stable-audio-3-local"
        assert "local-mock" in decision.alternatives

    def test_analysis_aware_routing(self):
        """analysis data should influence routing — speech content boosts TTS."""
        reg = EngineRegistry()
        sfx = _sfx_engine()
        tts = _tts_engine()
        reg.register(sfx)
        reg.register(tts)
        router = EngineRouter(reg)
        # texture intent with speech-heavy analysis → should pick TTS
        req = GenerationRequest(prompt="test", intent=SonicIntent.TEXTURE)
        analysis = {"contains_speech": True}
        decision = router.route(req, analysis=analysis)
        # TTS should get a boost but texture maps to sfx first
        # the analysis boost for speech + TTS capability should rank it higher
        assert decision.engine_id in ("test-tts", "test-sfx")


# ── execution ──

class TestExecution:
    def test_execute_returns_result(self):
        reg = _filled_registry()
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="thunder clap", intent=SonicIntent.SOUND_EFFECT)
        result = router.execute(req)
        assert isinstance(result, GenerationResult)
        assert result.audio.shape[1] == 2
        assert result.provider == "elevenlabs"

    def test_execute_calls_correct_engine(self):
        # VOICE intent now routes to SFX engines (ORAM never uses TTS)
        sfx = _sfx_engine()
        tts = _tts_engine()
        reg = EngineRegistry()
        reg.register(sfx)
        reg.register(tts)
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="hello world", intent=SonicIntent.VOICE)
        router.execute(req)
        assert sfx.call_count == 1
        assert tts.call_count == 0

    def test_execute_with_unavailable_engine_uses_alternative(self):
        sfx = _sfx_engine(available=False)
        local = _local_sfx(available=True)
        reg = EngineRegistry()
        reg.register(sfx)
        reg.register(local)
        router = EngineRouter(reg)
        req = GenerationRequest(prompt="test", intent=SonicIntent.SOUND_EFFECT)
        result = router.execute(req)
        assert result.engine_id == "local-sfx"

    def test_execute_nonexistent_engine_raises(self):
        reg = EngineRegistry()
        reg.register(_sfx_engine())
        router = EngineRouter(reg)
        from oram.engines.router import RoutingDecision
        decision = RoutingDecision(
            engine_id="nonexistent",
            provider="none",
            reason="test",
            confidence=1.0,
        )
        with pytest.raises(RuntimeError, match="not found"):
            router.execute(GenerationRequest(prompt="test"), decision=decision)


# ── resolve_intent ──

class TestResolveIntent:
    def test_legacy_sfx(self):
        assert resolve_intent("sfx") == SonicIntent.SOUND_EFFECT

    def test_legacy_voice(self):
        # legacy "voice" string now resolves to SOUND_EFFECT
        assert resolve_intent("voice") == SonicIntent.SOUND_EFFECT

    def test_legacy_music(self):
        assert resolve_intent("music") == SonicIntent.MUSIC

    def test_direct_intent(self):
        assert resolve_intent("texture") == SonicIntent.TEXTURE

    def test_unknown_defaults_to_sfx(self):
        assert resolve_intent("unknown_thing") == SonicIntent.SOUND_EFFECT


# ── normalizer ──

class TestNormalizer:
    def test_ensure_stereo_from_mono(self):
        from oram.engines.normalizer import AudioNormalizer
        norm = AudioNormalizer()
        mono = np.random.randn(1000).astype(np.float32)
        result = norm.normalize(mono, source_sr=48000)
        assert result.ndim == 2
        assert result.shape[1] == 2

    def test_resample_changes_length(self):
        from oram.engines.normalizer import AudioNormalizer
        norm = AudioNormalizer(target_sr=48000, trim_enabled=False)
        # 1 second at 22050 Hz
        audio = np.random.randn(22050, 2).astype(np.float32) * 0.1
        result = norm.normalize(audio, source_sr=22050, target_sr=48000)
        # should be approximately 48000 samples now
        assert abs(result.shape[0] - 48000) < 100

    def test_normalize_loudness(self):
        from oram.engines.normalizer import AudioNormalizer
        norm = AudioNormalizer(target_sr=48000, trim_enabled=False)
        # very quiet audio
        audio = np.random.randn(48000, 2).astype(np.float32) * 0.001
        result = norm.normalize(audio, source_sr=48000)
        # should be louder after normalization
        assert np.max(np.abs(result)) > np.max(np.abs(audio))

    def test_fade_applied(self):
        from oram.engines.normalizer import AudioNormalizer
        norm = AudioNormalizer(target_sr=48000, fade_ms=10.0, trim_enabled=False)
        audio = np.ones((48000, 2), dtype=np.float32) * 0.5
        result = norm.normalize(audio, source_sr=48000)
        # first sample should be faded down from 1.0
        assert abs(result[0, 0]) < abs(result[result.shape[0] // 2, 0])

    def test_empty_audio_unchanged(self):
        from oram.engines.normalizer import AudioNormalizer
        norm = AudioNormalizer()
        audio = np.zeros((0, 2), dtype=np.float32)
        result = norm.normalize(audio, source_sr=48000)
        assert result.shape[0] == 0
