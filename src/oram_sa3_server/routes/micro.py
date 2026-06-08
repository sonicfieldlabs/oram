from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter

from oram_sa3_server.identity import LEGACY_ENGINE_NAME, PRODUCT_NAME, SOUND_MATTER_CONCEPT, SOUND_MATTER_SCALES
from oram_sa3_server.registry import storage
from oram_sa3_server.routes.control import _analyze_features, _read_pcm16_wav, _resolve_output_wav
from oram_sa3_server.schemas import (
    ControlAudioAnalysisRequest,
    ControlFeatureSummary,
    MicroMatterProfileResult,
    MicroMatterRequest,
)
from oram_sa3_server.storage import safe_stem, utc_now_iso


router = APIRouter(prefix="/micro", tags=["micro"])

MICRO_FEATURES = [
    "envelope",
    "rms",
    "transient",
    "spectral_centroid",
    "onset_density",
    "tempo",
    "timbre",
    "pitch",
]


def _summary_map(summaries: list[ControlFeatureSummary]) -> dict[str, ControlFeatureSummary]:
    return {summary.feature: summary for summary in summaries}


def _summary_value(
    summaries: dict[str, ControlFeatureSummary],
    feature: str,
    key: str,
    fallback: float = 0.0,
) -> float:
    summary = summaries.get(feature)
    if not summary:
        return fallback
    value = getattr(summary, key, fallback)
    return float(value if value is not None else fallback)


def _micro_descriptors(
    *,
    summaries: list[ControlFeatureSummary],
    duration: float,
) -> dict[str, Any]:
    by_feature = _summary_map(summaries)
    transient_events = int(_summary_value(by_feature, "transient", "event_count"))
    onset_mean = _summary_value(by_feature, "onset_density", "mean")
    transient_mean = _summary_value(by_feature, "transient", "mean")
    spectral_mean = _summary_value(by_feature, "spectral_centroid", "mean")
    spectral_peak = _summary_value(by_feature, "spectral_centroid", "max")
    timbre_mean = _summary_value(by_feature, "timbre", "mean")
    rms_mean = _summary_value(by_feature, "rms", "mean")
    pitch_mean = _summary_value(by_feature, "pitch", "mean")
    grain_density = max(onset_mean, transient_mean)
    quanta_rate = transient_events / max(duration, 1e-9)
    cell_count = max(1, int(round((duration * 8.0) + (grain_density * duration * 90.0))))
    swarm_spread = max(0.0, min(1.0, (spectral_peak * 0.55) + (onset_mean * 0.30) + (rms_mean * 0.15)))
    return {
        "grain_density": round(grain_density, 6),
        "cell_count": cell_count,
        "transient_cells": transient_events,
        "quanta_rate": round(quanta_rate, 6),
        "swarm_spread": round(swarm_spread, 6),
        "spectral_tissue": {
            "centroid_mean": round(spectral_mean, 6),
            "centroid_peak": round(spectral_peak, 6),
            "timbre_mean": round(timbre_mean, 6),
        },
        "pitch_body": round(pitch_mean, 6),
        "amplitude_body": round(rms_mean, 6),
    }


def _module_suggestions(descriptors: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions = [
        {
            "module": "microscope",
            "reason": "Inspect density, transients, and spectral tissue before routing.",
        }
    ]
    if descriptors.get("grain_density", 0) >= 0.35:
        suggestions.append({"module": "grain_culture", "reason": "Dense material can be split into reusable grain cells."})
        suggestions.append({"module": "swarm", "reason": "High grain density can become a performable cloud."})
    if descriptors.get("transient_cells", 0) >= 4:
        suggestions.append({"module": "cell_splitter", "reason": "Transient cells are strong enough for micro-event slicing."})
    spectral = descriptors.get("spectral_tissue") if isinstance(descriptors.get("spectral_tissue"), dict) else {}
    if spectral.get("centroid_peak", 0) >= 0.45:
        suggestions.append({"module": "spectral_tissue", "reason": "Bright tissue supports freeze, smear, and mask gestures."})
    if descriptors.get("quanta_rate", 0) >= 8:
        suggestions.append({"module": "quanta", "reason": "Fast micro-events can be treated as time-frequency particles."})
    return suggestions


def _micro_profile_items(limit: int = 100) -> list[dict[str, Any]]:
    micro_dir = storage.settings.output_root / "micro"
    if not micro_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(micro_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        if len(items) >= limit:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            data["_profile_file"] = storage.relative_path(path)
            items.append(data)
    return items


@router.get("/matter-profiles")
def list_matter_profiles(limit: int = 100) -> dict[str, Any]:
    return {"profiles": _micro_profile_items(max(1, min(limit, 500)))}


@router.post("/matter-profile", response_model=MicroMatterProfileResult)
def create_matter_profile(request: MicroMatterRequest) -> MicroMatterProfileResult:
    source_path = _resolve_output_wav(request.input_audio_path)
    samples, channels, sample_rate, frame_count = _read_pcm16_wav(source_path)
    duration = frame_count / float(sample_rate)
    analysis_request = ControlAudioAnalysisRequest(
        input_audio_path=request.input_audio_path,
        metadata_path=request.metadata_path,
        source_id=request.source_id,
        features=MICRO_FEATURES,
        window_ms=request.window_ms,
        hop_ms=request.hop_ms,
        smooth=0.08,
        normalize=True,
        output_name=request.output_name,
        lineage=request.lineage,
    )
    feature_points, summaries = _analyze_features(
        samples=samples,
        channels=channels,
        sample_rate=sample_rate,
        frame_count=frame_count,
        request=analysis_request,
    )
    profile_id = f"micro_{uuid4().hex[:12]}"
    base = safe_stem(request.output_name, fallback=f"{Path(source_path).stem}_micro")
    micro_dir = storage.settings.output_root / "micro"
    profile_path = micro_dir / f"{base}_{profile_id}.json"
    descriptors = _micro_descriptors(summaries=summaries, duration=duration)
    suggestions = _module_suggestions(descriptors)
    parent_paths = [request.metadata_path] if request.metadata_path else []
    artifact = {
        "app": PRODUCT_NAME,
        "product": PRODUCT_NAME,
        "legacy_app": LEGACY_ENGINE_NAME,
        "concept": SOUND_MATTER_CONCEPT,
        "sound_matter_scales": SOUND_MATTER_SCALES,
        "type": "micro_matter_profile",
        "id": profile_id,
        "created_at": utc_now_iso(),
        "module": request.module,
        "input_audio_path": storage.relative_path(source_path),
        "metadata_path": request.metadata_path,
        "source_id": request.source_id,
        "sample_rate": sample_rate,
        "duration": duration,
        "window_ms": request.window_ms,
        "hop_ms": request.hop_ms,
        "features": feature_points,
        "summaries": [summary.model_dump(mode="json") for summary in summaries],
        "descriptors": descriptors,
        "module_suggestions": suggestions,
        "lineage": {
            **request.lineage,
            "id": profile_id,
            "parents": request.lineage.get("parents", []),
            "parent_metadata_paths": parent_paths,
            "operation": "micro_matter_profile",
            "source_type": "micro",
            "operation_params": {
                "module": request.module,
                "input_audio_path": storage.relative_path(source_path),
                "window_ms": request.window_ms,
                "hop_ms": request.hop_ms,
            },
        },
    }
    storage.write_json_atomic(profile_path, artifact, touch_library=True)
    return MicroMatterProfileResult(
        id=profile_id,
        status="done",
        input_audio_path=storage.relative_path(source_path),
        profile_file=storage.relative_path(profile_path),
        metadata_file=storage.relative_path(profile_path),
        sample_rate=sample_rate,
        duration=duration,
        module=request.module,
        descriptors=descriptors,
        module_suggestions=suggestions,
    )
