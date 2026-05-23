"""oram.ears.mix_context — multi-layer context for generation.

when generating a new sound, oram needs to know what's already playing.
this module analyzes all active layers and produces constraints:
what pitch space is occupied, what frequency ranges are empty,
what tempo and key the mix is in.

the generator uses these constraints to create sounds that dialogue
with the existing material rather than duplicate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from oram.ears.analyzer import AnalysisResult, analyze_buffer
from oram.ears.musical_features import (
    SPECTRAL_BAND_NAMES,
    MusicalFeatures,
    extract_musical_features,
)

# ---------------------------------------------------------------------------
# layer protocol — any object with these attrs is accepted
# ---------------------------------------------------------------------------

class _LayerLike(Protocol):
    """structural type for layer objects passed to build_mix_context."""

    is_empty: bool
    muted: bool
    buffer: np.ndarray
    sample_rate: int
    duration_seconds: float
    slot: int


# ---------------------------------------------------------------------------
# MixContext
# ---------------------------------------------------------------------------

@dataclass
class MixContext:
    """what the full mix sounds like — constraints for new generation."""

    dominant_pitches: list[str] = field(default_factory=list)
    key_estimate: str = ""
    key_confidence: float = 0.0
    bpm_estimate: float = 0.0
    bpm_confidence: float = 0.0
    spectral_centroid_avg: float = 0.0
    spectral_gaps: list[str] = field(default_factory=list)
    spectral_strengths: list[str] = field(default_factory=list)
    density_level: str = "sparse"
    active_layer_count: int = 0
    total_duration_range: tuple[float, float] = (0.0, 0.0)
    layer_summaries: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _layer_summary(slot: int, feat: MusicalFeatures,
                   analysis: AnalysisResult, duration: float) -> str:
    """one-line description of a layer's character."""
    parts: list[str] = [f"L{slot + 1}:"]

    # pitch info
    if feat.pitch_note and feat.pitch_confidence > 0.3:
        parts.append(feat.pitch_note)
    else:
        parts.append("no pitch")

    # tonal vs noisy
    if analysis.spectral_flatness > 0.5:
        parts.append("noisy")
    else:
        parts.append("tonal")

    # transient character
    if analysis.onset_density > 4.0:
        parts.append("percussive")

    # duration
    parts.append(f"{duration:.1f}s")

    return " ".join(parts)


def _empty_context() -> MixContext:
    """return a zero-filled context for sessions with no active layers."""
    return MixContext()


# ---------------------------------------------------------------------------
# build_mix_context
# ---------------------------------------------------------------------------

def build_mix_context(layers: list[Any], sample_rate: int) -> MixContext:
    """analyze all active layers and produce generation constraints.

    parameters
    ----------
    layers : list
        layer objects with ``is_empty``, ``muted``, ``buffer``,
        ``sample_rate``, ``duration_seconds``, and ``slot`` attributes.
    sample_rate : int
        fallback sample rate (each layer also carries its own).

    returns
    -------
    MixContext
        aggregated context describing what the mix sounds like.
    """
    # 1. filter to non-empty, non-muted layers
    active = [l for l in layers if not l.is_empty and not l.muted]
    if not active:
        return _empty_context()

    # 2. per-layer analysis
    features: list[MusicalFeatures] = []
    analyses: list[AnalysisResult] = []
    for layer in active:
        sr = getattr(layer, "sample_rate", sample_rate)
        feat = extract_musical_features(layer.buffer, sr)
        ana = analyze_buffer(layer.buffer, sr)
        features.append(feat)
        analyses.append(ana)

    # 3. aggregate ----------------------------------------------------------

    # dominant pitches (confidence > 0.3, deduplicated, ordered)
    seen_pitches: dict[str, float] = {}
    for feat in features:
        if feat.pitch_note and feat.pitch_confidence > 0.3:
            seen_pitches[feat.pitch_note] = max(
                seen_pitches.get(feat.pitch_note, 0.0), feat.pitch_confidence
            )
    dominant_pitches = sorted(seen_pitches, key=lambda p: seen_pitches[p],
                              reverse=True)

    # key estimate — pick the most confident across layers
    best_key = ""
    best_key_conf = 0.0
    for feat in features:
        if feat.key_confidence > best_key_conf:
            best_key = feat.key_estimate
            best_key_conf = feat.key_confidence

    # bpm — weighted average by confidence
    bpm_num = 0.0
    bpm_den = 0.0
    for feat in features:
        if feat.bpm > 0.0 and feat.bpm_confidence > 0.0:
            bpm_num += feat.bpm * feat.bpm_confidence
            bpm_den += feat.bpm_confidence
    bpm_estimate = bpm_num / bpm_den if bpm_den > 0.0 else 0.0
    bpm_confidence = bpm_den / len(features) if features else 0.0

    # spectral centroid — simple average
    centroids = [a.spectral_centroid_hz for a in analyses
                 if a.spectral_centroid_hz > 0.0]
    spectral_centroid_avg = float(np.mean(centroids)) if centroids else 0.0

    # spectral shape — average band vectors, then classify gaps / strengths
    spectral_gaps: list[str] = []
    spectral_strengths: list[str] = []
    band_vectors = [np.array(feat.spectral_shape) for feat in features
                    if len(feat.spectral_shape) == len(SPECTRAL_BAND_NAMES)]
    if band_vectors:
        avg_shape = np.mean(band_vectors, axis=0)
        for i, name in enumerate(SPECTRAL_BAND_NAMES):
            if avg_shape[i] < 0.08:
                spectral_gaps.append(name)
            elif avg_shape[i] > 0.2:
                spectral_strengths.append(name)

    # density — from average RMS
    rms_values = [a.rms for a in analyses]
    avg_rms = float(np.mean(rms_values)) if rms_values else 0.0
    if avg_rms < 0.05:
        density_level = "sparse"
    elif avg_rms < 0.2:
        density_level = "moderate"
    else:
        density_level = "dense"

    # duration range
    durations = [l.duration_seconds for l in active]
    duration_range = (min(durations), max(durations))

    # layer summaries
    summaries = [
        _layer_summary(layer.slot, feat, ana, layer.duration_seconds)
        for layer, feat, ana in zip(active, features, analyses)
    ]

    return MixContext(
        dominant_pitches=dominant_pitches,
        key_estimate=best_key,
        key_confidence=best_key_conf,
        bpm_estimate=bpm_estimate,
        bpm_confidence=bpm_confidence,
        spectral_centroid_avg=spectral_centroid_avg,
        spectral_gaps=spectral_gaps,
        spectral_strengths=spectral_strengths,
        density_level=density_level,
        active_layer_count=len(active),
        total_duration_range=duration_range,
        layer_summaries=summaries,
    )


# ---------------------------------------------------------------------------
# format_mix_constraints
# ---------------------------------------------------------------------------

def format_mix_constraints(ctx: MixContext) -> str:
    """format a MixContext as concise constraint text for the prompt compiler.

    the output is a short, human-readable string (< 200 chars) that tells
    the generator what sonic space is already occupied and where to fit in.
    """
    if ctx.active_layer_count == 0:
        return "empty mix — total freedom, any pitch/tempo/spectrum."

    parts: list[str] = []

    # density + key + tempo header
    header = f"complement a {ctx.density_level} mix"
    if ctx.key_estimate:
        header += f" in {ctx.key_estimate}"
    if ctx.bpm_estimate > 0.0:
        header += f" at ~{ctx.bpm_estimate:.0f} BPM"
    if ctx.spectral_centroid_avg > 0.0:
        header += f", centered at {ctx.spectral_centroid_avg:.0f}Hz"
    parts.append(header + ".")

    # spectral guidance
    if ctx.spectral_gaps:
        gap_str = " and ".join(ctx.spectral_gaps)
        parts.append(f"fill spectral gap in {gap_str} regions.")
    else:
        parts.append("balanced spectrum.")

    # pitch guidance
    if ctx.dominant_pitches:
        pitch_str = ", ".join(ctx.dominant_pitches[:4])
        parts.append(
            f"pitches present: {pitch_str}. avoid doubling these unless reinforcing."
        )
    else:
        parts.append("no dominant pitch detected — free pitch choice.")

    result = " ".join(parts)

    # hard trim to stay concise — prefer cutting the pitch tail
    if len(result) > 200:
        result = result[:197] + "..."

    return result
