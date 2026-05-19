"""oram.engines.adapter — provider-agnostic engine adapter protocol and data models.

EngineSpec: static metadata about what an engine can do.
GenerationRequest: what the user wants generated.
GenerationResult: what the engine produced.
OramEngineAdapter: the protocol every engine adapter must satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from oram.engines.capabilities import AudioCapability, EngineMode, EngineProvider, SonicIntent


@dataclass
class EngineSpec:
    """static metadata for a registered engine.

    declared once when the engine is registered; does not change per-request.
    """

    id: str                                    # e.g. "elevenlabs-sfx"
    provider: EngineProvider                   # who provides it
    label: str                                 # human-readable, e.g. "ElevenLabs Sound Effects"
    mode: EngineMode                           # cloud / local / hybrid
    capabilities: list[AudioCapability]        # what it can do
    requires_api_key: bool = True
    supports_streaming: bool = False
    supports_seed: bool = False
    supports_audio_input: bool = False
    max_duration_seconds: float = 30.0
    cost_per_second: float = 0.0               # rough estimate for routing decisions
    latency_profile: str = "medium"            # "fast" | "medium" | "slow"

    def has_capability(self, cap: AudioCapability) -> bool:
        """check if this engine supports a given capability."""
        return cap in self.capabilities

    def supports_intent(self, intent: SonicIntent) -> bool:
        """check if this engine can handle a sonic intent."""
        from oram.engines.capabilities import INTENT_CAPABILITY_MAP
        required = INTENT_CAPABILITY_MAP.get(intent, [])
        return any(self.has_capability(cap) for cap in required)


@dataclass
class GenerationRequest:
    """what the user wants generated.

    provider-agnostic: the router decides which engine handles it.
    """

    prompt: str
    intent: SonicIntent = SonicIntent.SOUND_EFFECT
    duration_seconds: float = 16.0

    # optional source audio for transform/analysis intents
    source_audio: np.ndarray | None = None
    source_sample_rate: int = 48000

    # explicit overrides (bypass auto-router)
    engine_id: str | None = None               # specific engine, e.g. "elevenlabs-sfx"
    provider: EngineProvider | None = None      # specific provider

    # general parameters
    parameters: dict = field(default_factory=dict)

    # provider-specific parameters (passed through to adapter)
    # elevenlabs
    voice_id: str | None = None
    model_id: str | None = None
    stability: float | None = None
    similarity_boost: float | None = None
    style: float | None = None
    force_instrumental: bool | None = None

    # stable audio / general
    seed: int | None = None
    negative_prompt: str | None = None
    guidance_scale: float | None = None

    def to_adapter_params(self) -> dict:
        """flatten provider-specific fields into a params dict for adapters."""
        params = dict(self.parameters)
        params["duration_seconds"] = self.duration_seconds
        if self.voice_id is not None:
            params["voice_id"] = self.voice_id
        if self.model_id is not None:
            params["model_id"] = self.model_id
        if self.stability is not None:
            params["stability"] = self.stability
        if self.similarity_boost is not None:
            params["similarity_boost"] = self.similarity_boost
        if self.style is not None:
            params["style"] = self.style
        if self.force_instrumental is not None:
            params["force_instrumental"] = self.force_instrumental
        if self.seed is not None:
            params["seed"] = self.seed
        if self.negative_prompt is not None:
            params["negative_prompt"] = self.negative_prompt
        if self.guidance_scale is not None:
            params["guidance_scale"] = self.guidance_scale
        return params


@dataclass
class GenerationResult:
    """what the engine produced.

    always stereo float32 numpy array + metadata.
    """

    audio: np.ndarray                          # stereo float32
    sample_rate: int
    engine_id: str
    provider: str
    prompt_used: str
    duration_seconds: float = 0.0
    cost_credits: float = 0.0
    cost_currency: str = "credits"
    parameters: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.duration_seconds == 0.0 and self.audio.shape[0] > 0:
            self.duration_seconds = self.audio.shape[0] / self.sample_rate


class OramEngineAdapter(Protocol):
    """protocol for all engine adapters.

    every adapter — ElevenLabs, Stability, HuggingFace, local, etc. —
    must implement this interface. the EngineRegistry uses it to
    register and route to engines.
    """

    spec: EngineSpec

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """generate audio from a request."""
        ...

    def is_available(self) -> bool:
        """check if the engine is available (API key set, service reachable, etc.)."""
        ...
