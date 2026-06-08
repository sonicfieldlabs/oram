from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from oram_sa3_server.registry import registry, settings, storage
from oram_sa3_server.schemas import (
    AudioToAudioRequest,
    ContinueRequest,
    GenerateRequest,
    GenerationResult,
    InpaintRequest,
    ModeId,
)


MODE_SPECS: dict[ModeId, tuple[type[BaseModel], str]] = {
    "text-to-audio": (GenerateRequest, "generate"),
    "audio-to-audio": (AudioToAudioRequest, "audio_to_audio"),
    "inpainting": (InpaintRequest, "inpaint"),
    "continuation": (ContinueRequest, "continue_audio"),
}


def run_provider_method(request_model: BaseModel, mode: str, method_name: str) -> GenerationResult:
    job_id = storage.new_job(mode, request_model.model_dump(exclude={"job_id"}))
    request_model = request_model.model_copy(update={"job_id": job_id})
    started = time.perf_counter()
    try:
        provider = registry.get(getattr(request_model, "provider"))
        method = getattr(provider, method_name)
        # Providers record their own result; no extra record_result needed.
        result = method(request_model)
        storage.update_job(
            job_id,
            metrics={"elapsed_seconds": round(time.perf_counter() - started, 6)},
        )
        return result
    except Exception as exc:
        result = storage.write_error_metadata(
            request=request_model,
            mode=mode,
            job_id=job_id,
            error=str(exc),
            provider=getattr(request_model, "provider", None),
            model=getattr(request_model, "model", None),
        )
        storage.update_job(
            job_id,
            metrics={"elapsed_seconds": round(time.perf_counter() - started, 6)},
        )
        return result


def request_model_for_mode(mode: ModeId, payload: dict[str, Any]) -> tuple[BaseModel, str]:
    model_cls, method_name = MODE_SPECS[mode]
    if mode == "inpainting" and isinstance(payload.get("inpaint_ranges"), str):
        payload = {**payload, "inpaint_ranges": parse_ranges_text(payload["inpaint_ranges"])}
    return model_cls(**payload), method_name


def run_provider_method_with_existing_job(
    request_model: BaseModel,
    *,
    job_id: str,
    mode: str,
    method_name: str,
) -> GenerationResult:
    request_model = request_model.model_copy(update={"job_id": job_id})
    job = storage.get_job(job_id)
    if job and job.status == "cancelled":
        return GenerationResult(
            job_id=job_id,
            status="error",
            error="job cancelled before execution",
            mode=mode,
        )
    storage.update_job(job_id, status="running")
    started = time.perf_counter()
    try:
        provider = registry.get(getattr(request_model, "provider"))
        method = getattr(provider, method_name)
        # Providers record their own result; no extra record_result needed.
        result = method(request_model)
        storage.update_job(
            job_id,
            metrics={"elapsed_seconds": round(time.perf_counter() - started, 6)},
        )
        return result
    except Exception as exc:
        result = storage.write_error_metadata(
            request=request_model,
            mode=mode,
            job_id=job_id,
            error=str(exc),
            provider=getattr(request_model, "provider", None),
            model=getattr(request_model, "model", None),
        )
        storage.update_job(
            job_id,
            metrics={"elapsed_seconds": round(time.perf_counter() - started, 6)},
        )
        return result


async def payload_from_json_or_form(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        data: dict[str, Any] = {}
        uploads: list[Any] = []
        for key, value in form.multi_items():
            if hasattr(value, "filename") and value.filename:
                uploads.append(value)
            else:
                data[key] = coerce_form_value(value, key=key)
        transient_upload = bool(
            data.pop("transient_upload", False) or data.pop("scratch_upload", False)
        )
        transient_paths: list[str] = []
        for value in uploads:
            try:
                saved, size = await storage.save_upload_stream(
                    filename=value.filename,
                    upload=value,
                    max_bytes=settings.max_upload_bytes,
                    directory=settings.scratch_dir if transient_upload else None,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=413,
                    detail=str(exc),
                ) from exc
            if size == 0:
                saved.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="uploaded audio is empty")
            data["input_audio_path"] = str(saved)
            if transient_upload:
                transient_paths.append(str(saved))
        if transient_paths:
            data["_transient_upload_paths"] = transient_paths
        return data
    try:
        return await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc


def coerce_form_value(value: Any, *, key: str | None = None) -> Any:
    if not isinstance(value, str):
        return value
    json_keys = {
        "lora",
        "tags",
        "lineage",
        "source",
        "latents",
        "ratings",
        "modulators",
        "semantic_layers",
        "semantic_effects",
        "generation_context",
        "region_roles",
        "preserve_ranges",
        "accent_ranges",
        "forbidden_ranges",
        "seed_ranges",
        "texture_ranges",
        "variation_ranges",
        "bridge_ranges",
        "silence_ranges",
        "genetic_identities",
        "generation_sequences",
    }
    if key in json_keys:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            if key == "tags":
                return [tag.strip() for tag in value.split(",") if tag.strip()]
            return value
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def pop_transient_upload_paths(payload: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    legacy_path = payload.pop("_transient_upload_path", None)
    if legacy_path:
        paths.append(Path(legacy_path))
    for item in payload.pop("_transient_upload_paths", []) or []:
        if item:
            paths.append(Path(item))
    return paths


def cleanup_transient_uploads(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def parse_ranges_text(value: str | list | None) -> list[tuple[float, float]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [tuple(item) for item in value]
    ranges: list[tuple[float, float]] = []
    for line in value.replace(";", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        pieces = [piece.strip() for piece in line.split(",")]
        if len(pieces) != 2:
            raise ValueError(f"invalid range '{line}', expected start,end")
        ranges.append((float(pieces[0]), float(pieces[1])))
    return ranges
