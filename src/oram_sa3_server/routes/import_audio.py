from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from oram_sa3_server.schemas import GenerateRequest, GenerationResult
from oram_sa3_server.registry import settings, storage
from oram_sa3_server.storage import safe_stem


router = APIRouter()


def _metadata_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="metadata must be valid JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")
    return data


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any, fallback: float) -> float:
    if value is None or value == "":
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


@router.post("/audio/import", response_model=GenerationResult)
async def import_audio(
    file: UploadFile = File(...),
    metadata: str = Form("{}"),
) -> GenerationResult:
    data = _metadata_dict(metadata)
    stem = safe_stem(data.get("output_name") or Path(file.filename or "audio").stem, fallback="import")
    suffix = Path(file.filename or "").suffix.lower() or ".wav"
    if suffix not in {".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".webm"}:
        suffix = ".wav"

    provider = data.get("provider") if data.get("provider") in {"mock", "stable_audio_python", "stable_audio_mlx", "stability_api"} else "mock"
    model = str(data.get("model") or "browser-import")
    duration = max(0.1, min(380.0, _safe_float(data.get("duration"), 0.1)))
    seed = _safe_int(data.get("seed"))
    seed = seed if seed is not None else -1
    steps = _safe_int(data.get("steps"))
    steps = steps if steps is not None else 1
    cfg_scale = _safe_float(data.get("cfg_scale"), 1.0)
    lineage = data.get("lineage") if isinstance(data.get("lineage"), dict) else {}
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    source_type = str(data.get("source_type") or lineage.get("source_type") or source.get("type") or "import")
    source = {**source, "type": source_type}

    request = GenerateRequest(
        provider=provider,
        model=model,
        prompt=str(data.get("prompt") or ""),
        negative_prompt=str(data.get("negative_prompt") or ""),
        duration=duration,
        steps=steps,
        cfg_scale=cfg_scale,
        seed=seed,
        batch_size=1,
        lora=_list_value(data.get("lora")),
        output_name=stem,
        culture_id=data.get("culture_id"),
        tags=[str(item) for item in _list_value(data.get("tags"))],
        notes=data.get("notes"),
        ratings=data.get("ratings") if isinstance(data.get("ratings"), dict) else {},
        waveform_preview=data.get("waveform_preview"),
        source=source,
        latents=data.get("latents") if isinstance(data.get("latents"), dict) else {},
        latent_file=data.get("latent_file"),
        latent_fingerprint=data.get("latent_fingerprint"),
        lineage={**lineage, "source_type": source_type},
    )
    try:
        uploaded_path, uploaded_size = await storage.save_upload_stream(
            filename=file.filename or f"{stem}{suffix}",
            upload=file,
            max_bytes=settings.max_upload_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    if uploaded_size == 0:
        uploaded_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="uploaded audio is empty")

    job_id = storage.new_job("audio-import", request.model_dump(exclude={"job_id"}))
    request = request.model_copy(update={"job_id": job_id})
    audio_path, metadata_path = storage.reserve_paths(
        request=request,
        mode="audio-import",
        job_id=job_id,
        extension=suffix,
    )[0]
    uploaded_path.replace(audio_path)
    sample_rate = _safe_int(data.get("sample_rate"))

    extra = {
        "imported": True,
        "source_type": source_type,
        "source": source,
        "organism": data.get("organism") if isinstance(data.get("organism"), dict) else None,
        "image": data.get("image") if isinstance(data.get("image"), dict) else None,
    }
    storage.write_metadata(
        metadata_path=metadata_path,
        request=request,
        mode="audio-import",
        provider=provider,
        model=model,
        seed=seed,
        output_audio_path=audio_path,
        sample_rate=sample_rate,
        status="done",
        extra=extra,
    )
    result = GenerationResult(
        job_id=job_id,
        status="done",
        audio_files=[storage.relative_path(audio_path)],
        metadata_files=[storage.relative_path(metadata_path)],
        seed=seed,
        duration=duration,
        sample_rate=sample_rate,
        provider=provider,
        model=model,
        mode="audio-import",
    )
    storage.record_result(result)
    return result
