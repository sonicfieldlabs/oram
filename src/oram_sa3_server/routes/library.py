from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter

from oram_sa3_server.identity import LEGACY_ENGINE_NAME, PRODUCT_NAME, SOUND_MATTER_CONCEPT
from oram_sa3_server.registry import settings, storage


router = APIRouter()

AUDIO_EXTENSIONS = {".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".webm"}
MAX_LIBRARY_ITEMS = 5000

# Cache the fully built library and rebuild only when the output tree changes.
_library_cache: dict[str, Any] = {
    "built_signature": None,
    "current_signature": None,
    "items": None,
}
_library_cache_lock = Lock()

# Parsed-metadata cache keyed by file path -> (mtime_ns, item). Lets a rebuild
# reuse already-parsed metadata for files that have not changed, instead of
# re-reading and re-parsing every JSON in the tree on every rebuild.
_metadata_item_cache: dict[str, tuple[int, dict[str, Any] | None]] = {}


def _output_signature() -> tuple[int, tuple[tuple[str, int, int], ...]]:
    """Cheap output/ fingerprint.

    Metadata writes explicitly increment storage.library_version. For files
    copied into output/ outside the app, directory mtime/size changes catch new
    archive audio without walking every audio file on every /library request.
    """
    root = settings.output_root
    directories: list[tuple[str, int, int]] = []
    if not root.exists():
        return (storage.library_version, tuple())
    for path in [root, *(item for item in root.iterdir() if item.is_dir())]:
        try:
            stat = path.stat()
        except OSError:
            continue
        directories.append((storage.relative_path(path), stat.st_mtime_ns, stat.st_size))
    return (storage.library_version, tuple(sorted(directories)))


def _cached_output_signature_unlocked() -> tuple[int, tuple[tuple[str, int, int], ...]]:
    # Stats only the top-level output directories (cheap) on every request, so a
    # file copied into output/ outside the app is noticed immediately. The costly
    # work — parsing metadata in _build_library_items — still runs only when this
    # signature actually changes.
    _library_cache["current_signature"] = _output_signature()
    return _library_cache["current_signature"]


def _audio_target(audio_path: str | None, absolute_audio_path: str | None = None) -> Path | None:
    if not audio_path and not absolute_audio_path:
        return None

    relative_target = settings.project_root / audio_path if audio_path else None
    absolute_target = Path(absolute_audio_path).expanduser() if absolute_audio_path else None

    # Prefer the current project-relative path. Older metadata can contain stale
    # absolute paths from a previous checkout location.
    if relative_target and relative_target.exists():
        return relative_target
    if absolute_target and absolute_target.exists():
        return absolute_target
    return relative_target or absolute_target


def _audio_source(path: Path) -> str:
    if settings.scratch_dir in path.parents:
        return "scratch"
    if settings.upload_dir in path.parents:
        return "upload"
    if settings.audio_dir in path.parents:
        return "output"
    try:
        return path.relative_to(settings.output_root).parts[0]
    except ValueError:
        return "output"


def _audio_item(path: Path) -> dict[str, Any]:
    stat = path.stat()
    relative = storage.relative_path(path)
    source = _audio_source(path)
    return {
        "id": path.stem,
        "app": PRODUCT_NAME,
        "product": PRODUCT_NAME,
        "legacy_app": LEGACY_ENGINE_NAME,
        "concept": SOUND_MATTER_CONCEPT,
        "provider": "local",
        "runtime": source,
        "model": None,
        "mode": "file",
        "technical_mode": "file",
        "germinator_mode": "archive",
        "prompt": None,
        "negative_prompt": None,
        "duration": None,
        "seed": None,
        "steps": None,
        "cfg_scale": None,
        "status": "done",
        "error": None,
        "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "culture_id": None,
        "tags": [source],
        "notes": None,
        "ratings": {},
        "waveform_preview": None,
        "latents": {},
        "latent_file": None,
        "latent_fingerprint": None,
        "organism": None,
        "image": None,
        "strain_stack": [],
        "source_type": source,
        "audio_file": relative,
        "metadata_file": None,
        "audio_exists": True,
        "sample_rate": None,
        "init_noise_level": None,
        "morph_depth": None,
        "inpaint_ranges": [],
        "lora": [],
        "lora_strains": [],
        "sound_id": relative,
        "parents": [],
        "children": [],
        "operation": "archive",
        "operation_params": {"source": source},
        "parent_branch": None,
        "source_region": None,
        "lineage": {
            "id": relative,
            "parents": [],
            "children": [],
            "operation": "archive",
            "operation_params": {"source": source},
            "audio_path": relative,
            "metadata_path": None,
        },
        "source": source,
        "file_size": stat.st_size,
    }


def _metadata_item(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    audio_path = data.get("output_audio_path")
    absolute_audio_path = data.get("absolute_output_audio_path")
    target = _audio_target(audio_path, absolute_audio_path)
    target_stat = None
    if target:
        try:
            target_stat = target.stat()
        except OSError:
            target_stat = None
    exists = target_stat is not None
    resolved_audio_path = storage.relative_path(target) if target and exists else audio_path
    lineage = data.get("lineage") if isinstance(data.get("lineage"), dict) else {}
    source_data = data.get("source") if isinstance(data.get("source"), dict) else {}
    source_type = data.get("source_type") or source_data.get("type")

    return {
        "id": path.stem,
        "app": data.get("app"),
        "provider": data.get("provider"),
        "runtime": data.get("runtime"),
        "model": data.get("model"),
        "mode": data.get("mode"),
        "technical_mode": data.get("technical_mode") or data.get("mode"),
        "germinator_mode": data.get("germinator_mode"),
        "prompt": data.get("prompt"),
        "negative_prompt": data.get("negative_prompt"),
        "duration": data.get("duration"),
        "seed": data.get("seed"),
        "steps": data.get("steps"),
        "cfg_scale": data.get("cfg_scale"),
        "status": data.get("status"),
        "error": data.get("error"),
        "created_at": data.get("created_at"),
        "culture_id": data.get("culture_id"),
        "tags": data.get("tags") or [],
        "notes": data.get("notes"),
        "ratings": data.get("ratings") or {},
        "waveform_preview": data.get("waveform_preview"),
        "latents": data.get("latents") if isinstance(data.get("latents"), dict) else {},
        "latent_file": data.get("latent_file"),
        "latent_fingerprint": data.get("latent_fingerprint"),
        "organism": data.get("organism") if isinstance(data.get("organism"), dict) else None,
        "image": data.get("image") if isinstance(data.get("image"), dict) else None,
        "audio_file": resolved_audio_path,
        "metadata_file": storage.relative_path(path),
        "audio_exists": exists,
        "sample_rate": data.get("sample_rate"),
        "init_noise_level": data.get("init_noise_level"),
        "morph_depth": data.get("morph_depth"),
        "inpaint_ranges": data.get("inpaint_ranges") or [],
        "lora": data.get("lora") or [],
        "lora_strains": data.get("lora_strains") or data.get("lora") or [],
        "strain_stack": data.get("strain_stack") or data.get("lora_strains") or data.get("lora") or [],
        "sound_id": data.get("sound_id") or lineage.get("id") or resolved_audio_path or path.stem,
        "parents": data.get("parents") or lineage.get("parents") or [],
        "children": data.get("children") or lineage.get("children") or [],
        "operation": data.get("operation") or lineage.get("operation") or data.get("germinator_mode"),
        "operation_params": data.get("operation_params") or lineage.get("operation_params") or {},
        "parent_branch": data.get("parent_branch") or lineage.get("parent_branch"),
        "source_region": data.get("source_region") or lineage.get("region"),
        "lineage": lineage,
        "source_type": source_type,
        "source": source_data or "metadata",
        "file_size": target_stat.st_size if target_stat else None,
    }


def _build_library_items() -> list[dict[str, Any]]:
    metadata_dir = settings.metadata_dir
    items: list[dict[str, Any]] = []
    indexed_audio: set[str] = set()
    if metadata_dir.exists():
        entries: list[tuple[Path, int]] = []
        for path in metadata_dir.glob("*.json"):
            try:
                mtime_ns = path.stat().st_mtime_ns
            except OSError:
                continue
            entries.append((path, mtime_ns))
        entries.sort(key=lambda entry: entry[1], reverse=True)
        seen: set[str] = set()
        for path, mtime_ns in entries[:MAX_LIBRARY_ITEMS]:
            key = str(path)
            seen.add(key)
            cached = _metadata_item_cache.get(key)
            if cached is not None and cached[0] == mtime_ns:
                item = cached[1]
            else:
                item = _metadata_item(path)
                _metadata_item_cache[key] = (mtime_ns, item)
            if item:
                items.append(item)
                if item.get("audio_file"):
                    indexed_audio.add(item["audio_file"])
        # Drop cache entries for metadata files that no longer exist.
        for stale_key in [key for key in _metadata_item_cache if key not in seen]:
            _metadata_item_cache.pop(stale_key, None)

    if settings.output_root.exists():
        audio_paths = (
            path
            for path in settings.output_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in AUDIO_EXTENSIONS
            and settings.metadata_dir not in path.parents
            and settings.scratch_dir not in path.parents
        )
        for path in sorted(audio_paths, key=lambda item: item.stat().st_mtime, reverse=True):
            relative = storage.relative_path(path)
            if relative in indexed_audio:
                continue
            items.append(_audio_item(path))
            indexed_audio.add(relative)

    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items[:MAX_LIBRARY_ITEMS]


@router.get("/library")
def list_library(limit: int = MAX_LIBRARY_ITEMS) -> dict[str, Any]:
    effective_limit = MAX_LIBRARY_ITEMS if limit <= 0 else max(1, min(limit, MAX_LIBRARY_ITEMS))

    with _library_cache_lock:
        signature = _cached_output_signature_unlocked()
        if _library_cache["built_signature"] != signature or _library_cache["items"] is None:
            _library_cache["items"] = _build_library_items()
            _library_cache["built_signature"] = signature

        items = _library_cache["items"][:effective_limit]
    return {
        "count": len(items),
        "items": items,
        "audio_dir": storage.relative_path(settings.audio_dir),
        "metadata_dir": storage.relative_path(settings.metadata_dir),
    }
