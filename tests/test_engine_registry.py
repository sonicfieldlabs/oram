"""tests for oram.engines.registry — engine registration and lookup."""

from __future__ import annotations

import numpy as np

from oram.engines.adapter import EngineSpec, GenerationRequest, GenerationResult
from oram.engines.capabilities import (
    AudioCapability,
    EngineMode,
    EngineProvider,
    SonicIntent,
)
from oram.engines.registry import EngineRegistry

# ── fixtures ──

class _MockEngine:
    """minimal OramEngineAdapter for testing."""

    def __init__(self, spec: EngineSpec, available: bool = True):
        self.spec = spec
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def generate(self, request: GenerationRequest) -> GenerationResult:
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


def _sfx_engine(available: bool = True) -> _MockEngine:
    return _MockEngine(
        EngineSpec(
            id="test-sfx",
            provider=EngineProvider.ELEVENLABS,
            label="Test SFX",
            mode=EngineMode.CLOUD,
            capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT],
        ),
        available=available,
    )


def _tts_engine(available: bool = True) -> _MockEngine:
    return _MockEngine(
        EngineSpec(
            id="test-tts",
            provider=EngineProvider.ELEVENLABS,
            label="Test TTS",
            mode=EngineMode.CLOUD,
            capabilities=[
                AudioCapability.TEXT_TO_SPEECH,
                AudioCapability.SPEECH_TO_SPEECH,
            ],
        ),
        available=available,
    )


def _music_engine(available: bool = True) -> _MockEngine:
    return _MockEngine(
        EngineSpec(
            id="test-music",
            provider=EngineProvider.STABILITY,
            label="Test Music",
            mode=EngineMode.CLOUD,
            capabilities=[AudioCapability.TEXT_TO_MUSIC],
        ),
        available=available,
    )


def _local_sfx(available: bool = True) -> _MockEngine:
    return _MockEngine(
        EngineSpec(
            id="local-sfx",
            provider=EngineProvider.LOCAL,
            label="Local SFX",
            mode=EngineMode.LOCAL,
            capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT],
            requires_api_key=False,
        ),
        available=available,
    )


# ── registration ──

class TestRegistration:
    def test_register_and_get(self):
        reg = EngineRegistry()
        engine = _sfx_engine()
        reg.register(engine)
        assert reg.get("test-sfx") is engine

    def test_get_nonexistent_returns_none(self):
        reg = EngineRegistry()
        assert reg.get("nope") is None

    def test_unregister(self):
        reg = EngineRegistry()
        reg.register(_sfx_engine())
        assert reg.engine_count == 1
        reg.unregister("test-sfx")
        assert reg.engine_count == 0
        assert reg.get("test-sfx") is None

    def test_unregister_nonexistent_is_safe(self):
        reg = EngineRegistry()
        reg.unregister("nope")  # should not raise

    def test_overwrite_warning(self, caplog):
        reg = EngineRegistry()
        reg.register(_sfx_engine())
        reg.register(_sfx_engine())  # same id again
        assert "overwriting" in caplog.text.lower()


# ── lookup ──

class TestLookup:
    def _filled_registry(self) -> EngineRegistry:
        reg = EngineRegistry()
        reg.register(_sfx_engine())
        reg.register(_tts_engine())
        reg.register(_music_engine())
        reg.register(_local_sfx())
        return reg

    def test_get_by_capability(self):
        reg = self._filled_registry()
        sfx_engines = reg.get_by_capability(AudioCapability.TEXT_TO_SOUND_EFFECT)
        ids = {e.spec.id for e in sfx_engines}
        assert "test-sfx" in ids
        assert "local-sfx" in ids
        assert "test-tts" not in ids

    def test_get_by_provider(self):
        reg = self._filled_registry()
        el_engines = reg.get_by_provider(EngineProvider.ELEVENLABS)
        ids = {e.spec.id for e in el_engines}
        assert ids == {"test-sfx", "test-tts"}

    def test_get_available_excludes_unavailable(self):
        reg = EngineRegistry()
        reg.register(_sfx_engine(available=True))
        reg.register(_tts_engine(available=False))
        available = reg.get_available()
        assert len(available) == 1
        assert available[0].spec.id == "test-sfx"

    def test_list_engines(self):
        reg = self._filled_registry()
        specs = reg.list_engines()
        assert len(specs) == 4
        ids = {s.id for s in specs}
        assert ids == {"test-sfx", "test-tts", "test-music", "local-sfx"}

    def test_list_capabilities(self):
        reg = self._filled_registry()
        caps = reg.list_capabilities()
        assert AudioCapability.TEXT_TO_SOUND_EFFECT in caps
        assert AudioCapability.TEXT_TO_SPEECH in caps
        assert AudioCapability.TEXT_TO_MUSIC in caps
        assert AudioCapability.SPEECH_TO_SPEECH in caps

    def test_engine_count(self):
        reg = self._filled_registry()
        assert reg.engine_count == 4

    def test_available_count(self):
        reg = EngineRegistry()
        reg.register(_sfx_engine(available=True))
        reg.register(_tts_engine(available=False))
        assert reg.available_count == 1


# ── summary ──

class TestSummary:
    def test_summary_no_engines(self):
        reg = EngineRegistry()
        assert reg.summary() == "no engines available"

    def test_summary_with_engines(self):
        reg = EngineRegistry()
        reg.register(_sfx_engine())
        reg.register(_music_engine())
        s = reg.summary()
        assert "elevenlabs" in s
        assert "stability" in s


# ── from_config ──

class TestFromConfig:
    def test_no_keys_gives_empty(self):
        """with no API keys, BYOK engines register as unavailable."""
        from oram.config import OramConfig
        config = OramConfig()
        config.elevenlabs_api_key = ""
        reg = EngineRegistry.from_config(config)
        assert reg.engine_count == 8  # seven ElevenLabs BYOK modes + local-mock
        assert reg.available_count == 1
        assert reg.get("elevenlabs-sfx") is not None
        assert reg.get("elevenlabs-sfx").is_available() is False
        assert reg.get("local-mock") is not None

    def test_elevenlabs_key_registers_engines(self):
        """with an ElevenLabs key, all ElevenLabs engines + local-mock should register."""
        from oram.config import OramConfig
        config = OramConfig()
        config.elevenlabs_api_key = "test-key-12345"
        reg = EngineRegistry.from_config(config)
        # should have 7 ElevenLabs + 1 local-mock = 8
        assert reg.engine_count == 8
        assert reg.get("elevenlabs-sfx") is not None
        assert reg.get("elevenlabs-tts") is not None
        assert reg.get("elevenlabs-music") is not None
        assert reg.get("elevenlabs-scribe") is not None
        assert reg.get("elevenlabs-voice-changer") is not None
        assert reg.get("elevenlabs-voice-design") is not None
        assert reg.get("elevenlabs-isolation") is not None
        assert reg.get("local-mock") is not None


# ── EngineSpec ──

class TestEngineSpec:
    def test_has_capability(self):
        spec = _sfx_engine().spec
        assert spec.has_capability(AudioCapability.TEXT_TO_SOUND_EFFECT)
        assert not spec.has_capability(AudioCapability.TEXT_TO_MUSIC)

    def test_supports_intent(self):
        spec = _sfx_engine().spec
        assert spec.supports_intent(SonicIntent.SOUND_EFFECT)
        # VOICE now redirects to TEXT_TO_SOUND_EFFECT — SFX engines handle it
        assert spec.supports_intent(SonicIntent.VOICE)

    def test_tts_supports_voice_and_transform(self):
        spec = _tts_engine().spec
        # VOICE intent now maps to TEXT_TO_SOUND_EFFECT (ORAM never uses TTS),
        # so a TTS-only engine no longer matches VOICE intent — correct by design.
        assert not spec.supports_intent(SonicIntent.VOICE)
        # TRANSFORM now only maps to AUDIO_TO_AUDIO (not SPEECH_TO_SPEECH)
        assert not spec.supports_intent(SonicIntent.TRANSFORM)
