"""oram.engines.registry — central engine registration and capability-based lookup.

the registry is the single source of truth for which engines are available.
engines register by capability, not by brand. lookup works by:
- engine ID (exact match)
- capability (what can do X?)
- provider (what does ElevenLabs offer?)
- availability (what's actually ready to use right now?)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from oram.engines.adapter import EngineSpec, OramEngineAdapter
from oram.engines.capabilities import AudioCapability, EngineProvider

if TYPE_CHECKING:
    from oram.config import OramConfig

log = logging.getLogger(__name__)


class EngineRegistry:
    """registers engines by capability, not by brand."""

    def __init__(self):
        self._engines: dict[str, OramEngineAdapter] = {}

    def register(self, adapter: OramEngineAdapter) -> None:
        """register an engine adapter."""
        engine_id = adapter.spec.id
        if engine_id in self._engines:
            log.warning("overwriting engine: %s", engine_id)
        self._engines[engine_id] = adapter
        log.info(
            "registered engine: %s (%s, %s, caps=%s)",
            engine_id,
            adapter.spec.provider.value,
            adapter.spec.mode.value,
            [c.value for c in adapter.spec.capabilities],
        )

    def unregister(self, engine_id: str) -> None:
        """remove an engine from the registry."""
        self._engines.pop(engine_id, None)

    def get(self, engine_id: str) -> OramEngineAdapter | None:
        """get an engine by its exact ID."""
        return self._engines.get(engine_id)

    def get_by_capability(self, capability: AudioCapability) -> list[OramEngineAdapter]:
        """get all engines that support a given capability."""
        return [
            adapter for adapter in self._engines.values()
            if adapter.spec.has_capability(capability)
        ]

    def get_by_provider(self, provider: EngineProvider) -> list[OramEngineAdapter]:
        """get all engines from a specific provider."""
        return [
            adapter for adapter in self._engines.values()
            if adapter.spec.provider == provider
        ]

    def get_available(self) -> list[OramEngineAdapter]:
        """get all engines that are currently available (API key set, etc.)."""
        return [
            adapter for adapter in self._engines.values()
            if adapter.is_available()
        ]

    def list_engines(self) -> list[EngineSpec]:
        """list specs for all registered engines."""
        return [adapter.spec for adapter in self._engines.values()]

    def list_available_engines(self) -> list[EngineSpec]:
        """list specs for all available engines."""
        return [adapter.spec for adapter in self.get_available()]

    def list_capabilities(self) -> set[AudioCapability]:
        """get the union of all capabilities across all available engines."""
        caps: set[AudioCapability] = set()
        for adapter in self.get_available():
            caps.update(adapter.spec.capabilities)
        return caps

    @property
    def engine_count(self) -> int:
        return len(self._engines)

    @property
    def available_count(self) -> int:
        return len(self.get_available())

    def summary(self) -> str:
        """human-readable summary of registered engines."""
        available = self.get_available()
        if not available:
            return "no engines available"

        providers: dict[str, list[str]] = {}
        for adapter in available:
            prov = adapter.spec.provider.value
            providers.setdefault(prov, []).append(adapter.spec.id)

        parts = []
        for prov, ids in sorted(providers.items()):
            parts.append(f"{prov}: {', '.join(ids)}")
        return " | ".join(parts)

    @classmethod
    def from_config(cls, config: OramConfig) -> EngineRegistry:
        """auto-register engines based on available API keys.

        checks which provider API keys are set and registers
        the corresponding engine adapters.
        """
        registry = cls()

        # elevenlabs — primary premium engine
        if config.elevenlabs_api_key:
            try:
                from oram.engines.elevenlabs import (
                    ElevenLabsIsolationEngine,
                    ElevenLabsMusicEngine,
                    ElevenLabsScribeEngine,
                    ElevenLabsSFXEngine,
                    ElevenLabsVoiceChangerEngine,
                    ElevenLabsVoiceDesignEngine,
                    ElevenLabsVoiceEngine,
                )

                registry.register(ElevenLabsSFXEngine(api_key=config.elevenlabs_api_key))
                registry.register(ElevenLabsVoiceEngine(api_key=config.elevenlabs_api_key))
                registry.register(ElevenLabsMusicEngine(api_key=config.elevenlabs_api_key))
                registry.register(ElevenLabsScribeEngine(api_key=config.elevenlabs_api_key))
                registry.register(ElevenLabsVoiceChangerEngine(api_key=config.elevenlabs_api_key))
                registry.register(ElevenLabsVoiceDesignEngine(api_key=config.elevenlabs_api_key))
                registry.register(ElevenLabsIsolationEngine(api_key=config.elevenlabs_api_key))
            except Exception as e:
                log.warning("failed to register ElevenLabs engines: %s", e)

        # stability — future sprint (direct API)
        if getattr(config, "stability_api_key", ""):
            log.info("stability API key detected — direct adapter not yet implemented")

        # huggingface — future sprint
        if getattr(config, "hf_token", ""):
            log.info("HuggingFace token detected — adapter not yet implemented")

        # fal — Stable Audio via fal.ai
        if getattr(config, "fal_key", ""):
            try:
                from oram.engines.stable_audio import StableAudioEngine
                registry.register(StableAudioEngine(api_key=config.fal_key))
            except Exception as e:
                log.warning("failed to register Stable Audio engine: %s", e)

        # replicate — future sprint
        if getattr(config, "replicate_api_token", ""):
            log.info("Replicate API token detected — adapter not yet implemented")

        # local engines — always register mock (no API key needed)
        try:
            from oram.engines.local_runner import LocalMockEngine
            registry.register(
                LocalMockEngine(sample_rate=getattr(config, "sample_rate", 48000))
            )
        except Exception as e:
            log.warning("failed to register local mock engine: %s", e)

        # local sidecar — register if sidecar host is configured
        sidecar_host = os.environ.get("ORAM_SIDECAR_HOST", "")
        if sidecar_host:
            try:
                from oram.engines.local_runner import LocalSidecarEngine

                sidecar_port = int(os.environ.get("ORAM_SIDECAR_PORT", "7860"))
                for model_id in ("tangoflux", "kokoro", "whisper", "essentia"):
                    engine = LocalSidecarEngine(
                        model_id=model_id,
                        host=sidecar_host,
                        port=sidecar_port,
                    )
                    registry.register(engine)
            except Exception as e:
                log.warning("failed to register sidecar engines: %s", e)

        return registry
