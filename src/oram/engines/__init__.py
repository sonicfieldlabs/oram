"""oram.engines — provider-agnostic audio engine registry and router.

the user selects creative intentions, not APIs.
each engine declares its capabilities, requirements, latency and cost profile.
ORAM routes each sonic request to the best available engine,
then normalizes the result into the looper timeline.
"""

from oram.engines.adapter import (
    EngineSpec,
    GenerationRequest,
    GenerationResult,
    OramEngineAdapter,
)
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

__all__ = [
    "AudioCapability",
    "EngineMode",
    "EngineProvider",
    "EngineRegistry",
    "EngineRouter",
    "EngineSpec",
    "GenerationRequest",
    "GenerationResult",
    "HealthStatus",
    "OramEngineAdapter",
    "RoutingDecision",
    "SonicIntent",
    "infer_intent_from_analysis",
    "resolve_intent",
    "select_engine_v2",
]
