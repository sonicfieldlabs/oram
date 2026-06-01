"""oram.engines.router — engine selection and execution router.

decides which engine handles a generation request based on:
1. explicit engine override (user said "use elevenlabs-sfx")
2. explicit provider override (user said "use elevenlabs")
3. intent-based auto-selection (user said "make a voice" → find best TTS engine)

routing considers: capability match, availability, cost, latency.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from oram.engines.adapter import GenerationRequest, GenerationResult
from oram.engines.capabilities import (
    INTENT_CAPABILITY_MAP,
    LEGACY_ENGINE_INTENT_MAP,
    AudioCapability,
    EngineProvider,
    SonicIntent,
)
from oram.engines.registry import EngineRegistry

log = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """transparent engine selection result."""

    engine_id: str
    provider: str
    reason: str
    confidence: float              # 0.0-1.0
    alternatives: list[str] = field(default_factory=list)
    intent: str = ""               # what intent was resolved


@dataclass
class HealthStatus:
    """engine availability + latency tracking."""

    engine_id: str
    available: bool = True
    last_check: float = 0.0        # monotonic timestamp
    last_latency_ms: float = 0.0
    error_count: int = 0
    success_count: int = 0

    @property
    def reliability(self) -> float:
        total = self.error_count + self.success_count
        if total == 0:
            return 1.0
        return self.success_count / total


class EngineRouter:
    """routes generation requests to the best available engine."""

    HISTORY_MAX = 50

    def __init__(self, registry: EngineRegistry, default_provider: str = ""):
        self._registry = registry
        self._default_provider = default_provider
        self._health: dict[str, HealthStatus] = {}
        self._history: list[RoutingDecision] = []

    def route(
        self,
        request: GenerationRequest,
        analysis: dict | None = None,
    ) -> RoutingDecision:
        """decide which engine should handle this request.

        priority:
        1. explicit engine_id override
        2. explicit provider override
        3. intent-based auto-selection
        """

        # 1. explicit engine override
        if request.engine_id:
            adapter = self._registry.get(request.engine_id)
            if adapter and adapter.is_available():
                return RoutingDecision(
                    engine_id=request.engine_id,
                    provider=adapter.spec.provider.value,
                    reason=f"user selected engine {request.engine_id}",
                    confidence=1.0,
                )
            # engine requested but not available — try alternatives
            log.warning("requested engine %s not available, routing by intent", request.engine_id)

        # 2. explicit provider override
        if request.provider:
            return self._route_by_provider(request.provider, request.intent)

        # 3. intent-based auto-selection
        return self._route_by_intent(request.intent, analysis)

    def execute(
        self,
        request: GenerationRequest,
        decision: RoutingDecision | None = None,
    ) -> GenerationResult:
        """route and execute a generation request.

        if no decision is provided, one is computed via route().
        """
        if decision is None:
            decision = self.route(request)
        self._record_decision(decision)

        adapter = self._registry.get(decision.engine_id)
        if adapter is None:
            raise RuntimeError(
                f"engine {decision.engine_id} not found in registry"
            )

        if not adapter.is_available():
            # try alternatives
            for alt_id in decision.alternatives:
                alt = self._registry.get(alt_id)
                if alt and alt.is_available():
                    log.info("falling back to alternative engine: %s", alt_id)
                    adapter = alt
                    break
            else:
                raise RuntimeError(
                    f"engine {decision.engine_id} and all alternatives unavailable"
                )

        log.info(
            "executing: engine=%s provider=%s reason=%s",
            decision.engine_id,
            decision.provider,
            decision.reason,
        )

        t0 = time.monotonic()
        try:
            result = adapter.generate(request)
            elapsed = (time.monotonic() - t0) * 1000
            self._record_success(decision.engine_id, elapsed)
            return result
        except Exception:
            self._record_error(decision.engine_id)
            raise

    # ── health tracking ──

    def _record_success(self, engine_id: str, latency_ms: float) -> None:
        health = self._health.setdefault(engine_id, HealthStatus(engine_id=engine_id))
        health.success_count += 1
        health.last_latency_ms = latency_ms
        health.available = True
        health.last_check = time.monotonic()

    def _record_error(self, engine_id: str) -> None:
        health = self._health.setdefault(engine_id, HealthStatus(engine_id=engine_id))
        health.error_count += 1
        health.last_check = time.monotonic()

    def _record_decision(self, decision: RoutingDecision) -> None:
        self._history.append(decision)
        if len(self._history) > self.HISTORY_MAX:
            self._history.pop(0)

    def get_health(self) -> dict[str, HealthStatus]:
        """return health status for all tracked engines."""
        return dict(self._health)

    def get_history(self, limit: int = 10) -> list[RoutingDecision]:
        """return last N routing decisions."""
        return list(self._history[-limit:])

    @property
    def registry(self) -> EngineRegistry:
        return self._registry

    def _route_by_provider(
        self,
        provider: EngineProvider,
        intent: SonicIntent,
    ) -> RoutingDecision:
        """find the best engine from a specific provider for the given intent."""
        engines = self._registry.get_by_provider(provider)
        available = [e for e in engines if e.is_available()]

        if not available:
            # fallback to any available engine for this intent
            return self._route_by_intent(intent)

        # prefer engines that match the intent
        matching = [e for e in available if e.spec.supports_intent(intent)]
        if matching:
            best = self._rank_engines(matching, intent)[0]
            alternatives = [e.spec.id for e in matching if e.spec.id != best.spec.id]
            return RoutingDecision(
                engine_id=best.spec.id,
                provider=provider.value,
                reason=f"best {provider.value} engine for {intent.value}",
                confidence=0.9,
                alternatives=alternatives,
            )

        # no intent match — use first available from this provider
        best = available[0]
        return RoutingDecision(
            engine_id=best.spec.id,
            provider=provider.value,
            reason=f"only available {provider.value} engine",
            confidence=0.5,
            alternatives=[e.spec.id for e in available[1:]],
        )

    def _route_by_intent(
        self,
        intent: SonicIntent,
        analysis: dict | None = None,
    ) -> RoutingDecision:
        """find the best engine for a sonic intent across all providers."""
        required_caps = INTENT_CAPABILITY_MAP.get(intent, [])
        if not required_caps:
            # unknown intent — try SFX as default
            required_caps = [AudioCapability.TEXT_TO_SOUND_EFFECT]

        # find all engines that have at least one matching capability
        candidates = []
        for cap in required_caps:
            for adapter in self._registry.get_by_capability(cap):
                if adapter.is_available() and adapter not in candidates:
                    candidates.append(adapter)

        if not candidates:
            # last resort: any available engine
            all_available = self._registry.get_available()
            if not all_available:
                raise RuntimeError("no engines available")
            best = all_available[0]
            return RoutingDecision(
                engine_id=best.spec.id,
                provider=best.spec.provider.value,
                reason="only available engine (no capability match)",
                confidence=0.3,
                alternatives=[e.spec.id for e in all_available[1:]],
            )

        # rank candidates
        ranked = self._rank_engines(candidates, intent, analysis)
        best = ranked[0]
        alternatives = [e.spec.id for e in ranked[1:]]

        return RoutingDecision(
            engine_id=best.spec.id,
            provider=best.spec.provider.value,
            reason=self._explain_choice(best, intent, analysis),
            confidence=self._compute_confidence(best, intent),
            alternatives=alternatives,
        )

    def _rank_engines(
        self,
        candidates: list,
        intent: SonicIntent,
        analysis: dict | None = None,
    ) -> list:
        """rank candidate engines by suitability for the given intent.

        scoring:
        - capability match breadth: +2 per matching capability
        - preferred provider: +3
        - cloud engines for quality intents: +1
        - local engines for experimental intents: +1
        - faster latency: +1
        - lower cost: +1
        """

        def score(adapter) -> float:
            s = 0.0
            spec = adapter.spec
            required_caps = INTENT_CAPABILITY_MAP.get(intent, [])

            # capability match breadth
            for cap in required_caps:
                if spec.has_capability(cap):
                    s += 2.0

            # preferred provider bonus
            if self._default_provider and spec.provider.value == self._default_provider:
                s += 3.0

            # Keep procedural generation as an explicit test/fallback engine.
            if spec.id == "local-mock":
                s -= 2.0

            # Prefer real local synthesis over the procedural mock when both are available.
            if spec.id.startswith("stable-audio") and spec.provider.value == "local":
                s += 1.5

            # cloud for production, local for experimental
            if intent in (SonicIntent.VOICE, SonicIntent.MUSIC):
                if spec.mode.value == "cloud":
                    s += 1.0
            elif intent == SonicIntent.TEXTURE:
                if spec.mode.value == "local":
                    s += 0.5

            # latency
            latency_scores = {"fast": 1.0, "medium": 0.5, "slow": 0.0}
            s += latency_scores.get(spec.latency_profile, 0.5)

            # lower cost preferred (inverse)
            if spec.cost_per_second > 0:
                s -= min(spec.cost_per_second / 100, 1.0)

            # analysis-aware boosting
            if analysis:
                if analysis.get("contains_speech") and spec.has_capability(AudioCapability.TEXT_TO_SOUND_EFFECT):
                    s += 2.0  # ORAM never generates speech — boost sfx for vocal content
                if analysis.get("rhythmic_regularity", 0) > 0.6 and spec.has_capability(AudioCapability.TEXT_TO_MUSIC):
                    s += 1.5
                if analysis.get("is_noisy") and spec.has_capability(AudioCapability.TEXT_TO_SOUND_EFFECT):
                    s += 1.0

            return s

        return sorted(candidates, key=score, reverse=True)

    def _explain_choice(self, adapter, intent: SonicIntent, analysis: dict | None) -> str:
        """build a human-readable explanation for the routing decision."""
        parts = [f"{adapter.spec.label} for {intent.value}"]
        if adapter.spec.mode.value != "cloud":
            parts.append(f"({adapter.spec.mode.value})")
        caps = [c.value for c in adapter.spec.capabilities]
        parts.append(f"caps: {', '.join(caps[:3])}")
        return " — ".join(parts)

    def _compute_confidence(self, adapter, intent: SonicIntent) -> float:
        """compute routing confidence based on capability match."""
        required = INTENT_CAPABILITY_MAP.get(intent, [])
        if not required:
            return 0.5
        matched = sum(1 for cap in required if adapter.spec.has_capability(cap))
        return min(0.5 + (matched / len(required)) * 0.5, 1.0)


def resolve_intent(engine_hint: str) -> SonicIntent:
    """resolve a legacy engine name or intent string to a SonicIntent.

    handles backward-compat with v1 engine names like "sfx", "voice", "music"
    and also accepts full intent names like "sound_effect", "texture".
    """
    # try legacy map first
    if engine_hint in LEGACY_ENGINE_INTENT_MAP:
        return LEGACY_ENGINE_INTENT_MAP[engine_hint]

    # try as SonicIntent value
    try:
        return SonicIntent(engine_hint)
    except ValueError:
        pass

    # default
    return SonicIntent.SOUND_EFFECT


def infer_intent_from_analysis(analysis: dict) -> SonicIntent:
    """infer SonicIntent from a listening analysis dict.

    integrates with the ears/ listening pipeline. the analysis dict
    contains keys from TechnicalRoute: contains_speech, contains_voice,
    pitch_confidence, rhythmic_regularity, is_noisy, is_gestural.

    this replaces the legacy gateway/router.py select_engine() function
    with a provider-agnostic intent inference.
    """
    contains_speech = analysis.get("contains_speech", False)
    contains_voice = analysis.get("contains_voice", False)
    pitch_confidence = analysis.get("pitch_confidence", 0.0)
    rhythmic = analysis.get("rhythmic_regularity", 0.0)

    # voice detection — ORAM never generates speech, route to sfx instead
    if contains_speech or contains_voice:
        return SonicIntent.SOUND_EFFECT

    # music detection — tonal + rhythmic content
    if pitch_confidence > 0.65 or rhythmic > 0.7:
        return SonicIntent.MUSIC

    # texture — tonal but not rhythmic
    if pitch_confidence > 0.4 and rhythmic < 0.3:
        return SonicIntent.TEXTURE

    # default — sound effect for gestural, noisy, material sounds
    return SonicIntent.SOUND_EFFECT


def select_engine_v2(
    analysis: dict,
    user_mode: str = "auto",
    router: EngineRouter | None = None,
) -> RoutingDecision:
    """legacy bridge: replaces gateway/router.select_engine().

    translates the old analysis-dict + user-mode interface into
    the new EngineRouter system. if no router is provided,
    returns a minimal decision using intent inference only.
    """
    # explicit user override
    if user_mode in LEGACY_ENGINE_INTENT_MAP:
        intent = resolve_intent(user_mode)
    elif user_mode == "auto":
        intent = infer_intent_from_analysis(analysis)
    else:
        intent = SonicIntent.SOUND_EFFECT

    if router is not None:
        request = GenerationRequest(
            prompt="",  # prompt not needed for routing
            intent=intent,
        )
        return router.route(request, analysis=analysis)

    # fallback without router
    return RoutingDecision(
        engine_id=user_mode if user_mode != "auto" else intent.value,
        provider="unknown",
        reason=f"intent inferred: {intent.value}",
        confidence=0.6,
        intent=intent.value,
    )
