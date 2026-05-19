"""oram.gateway.router — engine selection logic.

decides which ElevenLabs engine to use based on audio analysis
and user preferences. the decision is always transparent and inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EngineDecision:
    """transparent engine selection result."""

    engine: str  # sfx / voice / music
    reason: str
    confidence: float  # 0.0-1.0


def select_engine(analysis: dict, user_mode: str = "auto") -> EngineDecision:
    """select the best engine based on analysis and user preference.

    analysis dict should contain keys from the listening system:
    - contains_speech: bool
    - contains_voice: bool
    - pitch_confidence: float (0.0-1.0)
    - rhythmic_regularity: float (0.0-1.0)
    - is_gestural: bool
    - is_noisy: bool
    - duration: float
    - spectral_centroid: float

    user_mode: "auto" | "sfx" | "voice" | "music"
    """

    # explicit user override
    if user_mode in ("sfx", "voice", "music"):
        return EngineDecision(
            engine=user_mode,
            reason=f"user selected {user_mode}",
            confidence=1.0,
        )

    # auto selection logic
    contains_speech = analysis.get("contains_speech", False)
    contains_voice = analysis.get("contains_voice", False)
    pitch_confidence = analysis.get("pitch_confidence", 0.0)
    rhythmic = analysis.get("rhythmic_regularity", 0.0)

    # voice detection
    if contains_speech or contains_voice:
        return EngineDecision(
            engine="voice",
            reason="detected speech or vocal content",
            confidence=0.8 if contains_speech else 0.6,
        )

    # music detection
    if pitch_confidence > 0.65 or rhythmic > 0.7:
        reasons = []
        if pitch_confidence > 0.65:
            reasons.append(f"pitch confidence {pitch_confidence:.2f}")
        if rhythmic > 0.7:
            reasons.append(f"rhythmic regularity {rhythmic:.2f}")
        return EngineDecision(
            engine="music",
            reason=f"tonal/rhythmic content: {', '.join(reasons)}",
            confidence=max(pitch_confidence, rhythmic),
        )

    # default to SFX for gestural, noisy, material sounds
    reason_parts = []
    if analysis.get("is_noisy", False):
        reason_parts.append("noisy")
    if analysis.get("is_gestural", False):
        reason_parts.append("gestural")
    reason_parts.append("non-vocal")
    reason_parts.append("material event")

    return EngineDecision(
        engine="sfx",
        reason=f"{', '.join(reason_parts)}",
        confidence=0.7,
    )


def format_decision(decision: EngineDecision) -> str:
    """format engine decision for display."""
    return f"selected engine: {decision.engine} — reason: {decision.reason}"
