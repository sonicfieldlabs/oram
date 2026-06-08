from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from oram_sa3_server.config import Settings
from oram_sa3_server.identity import (
    LEGACY_ENGINE_NAME,
    PRODUCT_NAME,
    SOUND_MATTER_CONCEPT,
    SOUND_MATTER_SCALES,
)
from oram_sa3_server.schemas import GenerationResult, JobStatus


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_stem(value: str | None, fallback: str = "sa3_output") -> str:
    if not value:
        value = fallback
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return stem[:80] or fallback


def safe_suffix(value: str | None, fallback: str = ".wav") -> str:
    if not value:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", value.lstrip("."))[:12]
    return f".{cleaned}" if cleaned else fallback


MAX_TRACKED_JOBS = 500
AUDIO_EXTENSIONS = {".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".webm"}
MODEL_FILE_EXTENSIONS = {".bin", ".ckpt", ".gguf", ".mlmodel", ".pt", ".pth", ".safetensors"}
UPLOAD_CHUNK_SIZE = 1024 * 1024


class StorageManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.audio_dir = settings.audio_dir
        self.metadata_dir = settings.metadata_dir
        self.upload_dir = settings.upload_dir
        self.scratch_dir = settings.scratch_dir
        self.jobs: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        self.library_version = 0
        self.ensure_dirs()

    def _evict_old_jobs(self) -> None:
        # dict preserves insertion order; drop the oldest TERMINAL jobs past the
        # cap. Running/queued jobs are protected so a flurry of mock requests
        # cannot evict a long-running job that a WebSocket is still polling.
        with self._lock:
            if len(self.jobs) <= MAX_TRACKED_JOBS:
                return
            terminal = {"done", "error", "cancelled"}
            for job_id, job in list(self.jobs.items()):
                if len(self.jobs) <= MAX_TRACKED_JOBS:
                    return
                if job.get("status") in terminal:
                    self.jobs.pop(job_id, None)

    def ensure_dirs(self) -> None:
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.scratch_dir.mkdir(parents=True, exist_ok=True)

    def touch_library(self) -> None:
        with self._lock:
            self.library_version += 1

    def new_job(
        self,
        mode: str,
        request_data: dict[str, Any],
        *,
        status: str = "running",
    ) -> str:
        job_id = str(uuid4())
        timestamp = utc_now_iso()
        with self._lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "status": status,
                "mode": mode,
                "provider": request_data.get("provider"),
                "model": request_data.get("model"),
                "request": request_data,
                "audio_files": [],
                "metadata_files": [],
                "error": None,
                "metrics": {},
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            self._evict_old_jobs()
        return job_id

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        error: str | None = None,
        audio_files: list[str] | None = None,
        metadata_files: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            if status is not None:
                job["status"] = status
            if error is not None:
                job["error"] = error
            if audio_files is not None:
                job["audio_files"] = audio_files
            if metadata_files is not None:
                job["metadata_files"] = metadata_files
            if metrics is not None:
                current_metrics = job.get("metrics") if isinstance(job.get("metrics"), dict) else {}
                job["metrics"] = {**current_metrics, **metrics}
            job["updated_at"] = utc_now_iso()

    def relative_path(self, path: str | Path) -> str:
        path = Path(path).resolve()
        try:
            return path.relative_to(self.settings.project_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def absolute_path(self, path: str | Path) -> str:
        path = Path(path)
        if not path.is_absolute():
            path = self.settings.project_root / path
        return path.resolve().as_posix()

    def resolve_path(self, path: str | Path) -> Path:
        path = Path(path).expanduser()
        if not path.is_absolute():
            path = self.settings.project_root / path
        return path.resolve()

    def resolve_existing_path(self, path: str | Path, *, label: str = "path") -> Path:
        resolved = self.resolve_path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
        return resolved

    @staticmethod
    def is_within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            return False
        return True

    def resolve_existing_metadata_path(self, path: str | Path, *, label: str = "metadata") -> Path:
        resolved = self.resolve_existing_path(path, label=label)
        if not self.is_within(resolved, self.metadata_dir):
            raise PermissionError(f"{label} must be inside the metadata directory: {path}")
        return resolved

    def resolve_existing_input_audio_path(self, path: str | Path, *, label: str = "input audio") -> Path:
        resolved = self.resolve_existing_path(path, label=label)
        allowed_roots = self.settings.allowed_input_roots
        if not any(self.is_within(resolved, root) for root in allowed_roots):
            roots = ", ".join(self.relative_path(root) for root in allowed_roots)
            raise PermissionError(f"{label} must be inside an allowed input root ({roots}): {path}")
        if resolved.suffix.lower() not in AUDIO_EXTENSIONS:
            raise ValueError(f"{label} must be an audio file: {path}")
        return resolved

    def resolve_existing_model_file_path(self, path: str | Path, *, label: str = "model file") -> Path:
        resolved = self.resolve_existing_path(path, label=label)
        allowed_roots = self.settings.allowed_model_roots
        if not any(self.is_within(resolved, root) for root in allowed_roots):
            roots = ", ".join(self.relative_path(root) for root in allowed_roots)
            raise PermissionError(f"{label} must be inside an allowed model root ({roots}): {path}")
        if resolved.suffix.lower() not in MODEL_FILE_EXTENSIONS:
            raise ValueError(f"{label} has an unsupported extension: {path}")
        return resolved

    @staticmethod
    def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)

    def write_json_atomic(
        self,
        path: str | Path,
        data: dict[str, Any],
        *,
        touch_library: bool = False,
    ) -> None:
        with self._lock:
            self._write_json_atomic(Path(path), data)
            if touch_library:
                self.library_version += 1

    def reserve_paths(
        self,
        *,
        request: BaseModel,
        mode: str,
        job_id: str,
        count: int = 1,
        extension: str = ".wav",
    ) -> list[tuple[Path, Path]]:
        data = request.model_dump(exclude={"job_id"})
        prompt = data.get("prompt", "")
        fallback = f"{mode}_{safe_stem(prompt, 'audio')}"
        base = safe_stem(data.get("output_name"), fallback=fallback)
        short = job_id.split("-")[0]

        paths: list[tuple[Path, Path]] = []
        for index in range(count):
            suffix = "" if count == 1 else f"_{index + 1:02d}"
            stem = f"{base}_{short}{suffix}"
            audio_path = self.audio_dir / f"{stem}{extension}"
            metadata_path = self.metadata_dir / f"{stem}.json"
            paths.append((audio_path, metadata_path))
        return paths

    def write_metadata(
        self,
        *,
        metadata_path: str | Path,
        request: BaseModel,
        mode: str,
        provider: str,
        model: str,
        seed: int | None,
        output_audio_path: str | Path | None,
        sample_rate: int | None,
        status: str,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_data = request.model_dump(exclude={"job_id"})
        request_data["lora"] = self._compact_lora_specs(request_data.get("lora", []))
        runtime = self._runtime_from_provider(provider)
        germinator_mode = self._germinator_mode(mode)
        lineage = self._lineage_metadata(
            request_data=request_data,
            mode=mode,
            germinator_mode=germinator_mode,
            provider=provider,
            model=model,
            seed=seed,
            output_audio_path=output_audio_path,
            metadata_path=metadata_path,
        )
        lora_strains = []
        for item in request_data.get("lora", []):
            if not isinstance(item, dict):
                continue
            strain = {
                key: item.get(key)
                for key in (
                    "id",
                    "name",
                    "path",
                    "strength",
                    "tags",
                    "author",
                    "license",
                    "source_dataset",
                    "prompt_vocabulary",
                    "recommended_modules",
                    "provenance_notes",
                    "metadata",
                )
                if item.get(key) not in (None, "", [], {})
            }
            strain["enabled"] = item.get("enabled", True)
            lora_strains.append(strain)
        raw_source = request_data.get("source") if isinstance(request_data.get("source"), dict) else {}
        latent_file = request_data.get("latent_file")
        latent_fingerprint = request_data.get("latent_fingerprint")
        latents = request_data.get("latents") if isinstance(request_data.get("latents"), dict) else {}
        if latent_file or latent_fingerprint:
            latents = {
                **latents,
                "file": latent_file or latents.get("file"),
                "fingerprint": latent_fingerprint or latents.get("fingerprint"),
            }
        if not latents and output_audio_path:
            latents = {
                "status": "deferred",
                "encoder": "SAME",
                "source_audio_path": self.relative_path(output_audio_path),
            }
        source_metadata = {
            **raw_source,
            "type": lineage.get("source_type")
            or raw_source.get("type")
            or ("audio" if request_data.get("input_audio_path") else "prompt"),
            "audio_path": request_data.get("input_audio_path") or raw_source.get("audio_path"),
            "parent_ids": lineage["parents"],
            "parent_branch": lineage.get("parent_branch"),
        }
        metadata = {
            "app": PRODUCT_NAME,
            "product": PRODUCT_NAME,
            "legacy_app": LEGACY_ENGINE_NAME,
            "concept": SOUND_MATTER_CONCEPT,
            "sound_matter_scales": SOUND_MATTER_SCALES,
            "engine": self.settings.engine_name,
            "provider": provider,
            "runtime": runtime,
            "model": model,
            "mode": mode,
            "technical_mode": mode,
            "germinator_mode": germinator_mode,
            "prompt": request_data.get("prompt"),
            "negative_prompt": request_data.get("negative_prompt"),
            "base_prompt": request_data.get("base_prompt"),
            "modulated_prompt": request_data.get("modulated_prompt"),
            "base_negative_prompt": request_data.get("base_negative_prompt"),
            "modulated_negative_prompt": request_data.get("modulated_negative_prompt"),
            "modulators": request_data.get("modulators", []),
            "semantic_layers": request_data.get("semantic_layers", []),
            "semantic_effects": request_data.get("semantic_effects", []),
            "generation_context": request_data.get("generation_context", {}),
            "control_routes": request_data.get("control_routes", []),
            "control_snapshots": request_data.get("control_snapshots", []),
            "control_sources": request_data.get("control_sources", []),
            "prompt_weight": request_data.get("prompt_weight"),
            "negative_prompt_weight": request_data.get("negative_prompt_weight"),
            "seed_drift": request_data.get("seed_drift"),
            "batch_spread": request_data.get("batch_spread"),
            "inpaint_density": request_data.get("inpaint_density"),
            "mask_feather": request_data.get("mask_feather"),
            "continuation_divergence": request_data.get("continuation_divergence"),
            "brightness_language": request_data.get("brightness_language"),
            "lora_strength": request_data.get("lora_strength"),
            "region_roles": request_data.get("region_roles", []),
            "preserve_ranges": request_data.get("preserve_ranges", []),
            "accent_ranges": request_data.get("accent_ranges", []),
            "forbidden_ranges": request_data.get("forbidden_ranges", []),
            "seed_ranges": request_data.get("seed_ranges", []),
            "texture_ranges": request_data.get("texture_ranges", []),
            "variation_ranges": request_data.get("variation_ranges", []),
            "bridge_ranges": request_data.get("bridge_ranges", []),
            "silence_ranges": request_data.get("silence_ranges", []),
            "genetic_identities": request_data.get("genetic_identities", []),
            "generation_sequences": request_data.get("generation_sequences", []),
            "source": source_metadata,
            "source_type": source_metadata["type"],
            "duration": request_data.get("duration"),
            "steps": request_data.get("steps"),
            "cfg_scale": request_data.get("cfg_scale"),
            "seed": seed,
            "batch_size": request_data.get("batch_size"),
            "init_noise_level": request_data.get("init_noise_level"),
            "morph_depth": request_data.get("init_noise_level"),
            "inpaint_ranges": request_data.get("inpaint_ranges", []),
            "input_audio_path": request_data.get("input_audio_path"),
            "source_audio_path": request_data.get("input_audio_path"),
            "output_audio_path": (
                self.relative_path(output_audio_path) if output_audio_path else None
            ),
            "absolute_output_audio_path": self.absolute_path(output_audio_path)
            if output_audio_path
            else None,
            "metadata_path": self.relative_path(metadata_path),
            "absolute_metadata_path": self.absolute_path(metadata_path),
            "sample_rate": sample_rate,
            "created_at": utc_now_iso(),
            "status": status,
            "error": error,
            "lora": request_data.get("lora", []),
            "lora_strains": lora_strains,
            "strain_stack": lora_strains,
            "culture_id": request_data.get("culture_id"),
            "tags": request_data.get("tags", []),
            "notes": request_data.get("notes") or "",
            "ratings": request_data.get("ratings") if isinstance(request_data.get("ratings"), dict) else {},
            "latents": latents,
            "latent_file": latent_file or latents.get("file"),
            "latent_fingerprint": latent_fingerprint or latents.get("fingerprint"),
            "waveform_preview": request_data.get("waveform_preview") or (
                {
                    "type": "deferred",
                    "audio_path": self.relative_path(output_audio_path),
                }
                if output_audio_path
                else None
            ),
            "audio": {
                "path": self.relative_path(output_audio_path) if output_audio_path else None,
                "absolute_path": self.absolute_path(output_audio_path) if output_audio_path else None,
                "sample_rate": sample_rate,
                "duration": request_data.get("duration"),
            },
            "sound_id": lineage["id"],
            "parents": lineage["parents"],
            "children": lineage["children"],
            "operation": lineage["operation"],
            "operation_params": lineage["operation_params"],
            "parent_branch": lineage.get("parent_branch"),
            "source_region": lineage.get("region"),
            "lineage": lineage,
            "request": request_data if status == "error" else None,
        }
        if extra:
            metadata.update(extra)

        metadata_path = Path(metadata_path)
        with self._lock:
            self._write_json_atomic(metadata_path, metadata)
            self.library_version += 1
            if output_audio_path:
                self._append_lineage_child(
                    lineage.get("parent_metadata_paths") or [],
                    lineage["id"],
                )
        return metadata

    def _lineage_metadata(
        self,
        *,
        request_data: dict[str, Any],
        mode: str,
        germinator_mode: str,
        provider: str,
        model: str,
        seed: int | None,
        output_audio_path: str | Path | None,
        metadata_path: str | Path,
    ) -> dict[str, Any]:
        raw = request_data.get("lineage") if isinstance(request_data.get("lineage"), dict) else {}
        sound_id = str(raw.get("id") or f"sound_{Path(metadata_path).stem}")
        parents = self._string_list(raw.get("parents"))
        children = self._string_list(raw.get("children"))
        operation = str(raw.get("operation") or germinator_mode or mode)
        operation_params = raw.get("operation_params") if isinstance(raw.get("operation_params"), dict) else {}
        operation_params = {
            "prompt": request_data.get("prompt"),
            "negative_prompt": request_data.get("negative_prompt"),
            "base_prompt": request_data.get("base_prompt"),
            "modulated_prompt": request_data.get("modulated_prompt"),
            "base_negative_prompt": request_data.get("base_negative_prompt"),
            "modulated_negative_prompt": request_data.get("modulated_negative_prompt"),
            "modulators": request_data.get("modulators", []),
            "semantic_layers": request_data.get("semantic_layers", []),
            "semantic_effects": request_data.get("semantic_effects", []),
            "generation_context": request_data.get("generation_context", {}),
            "control_routes": request_data.get("control_routes", []),
            "control_snapshots": request_data.get("control_snapshots", []),
            "control_sources": request_data.get("control_sources", []),
            "prompt_weight": request_data.get("prompt_weight"),
            "negative_prompt_weight": request_data.get("negative_prompt_weight"),
            "seed_drift": request_data.get("seed_drift"),
            "batch_spread": request_data.get("batch_spread"),
            "inpaint_density": request_data.get("inpaint_density"),
            "mask_feather": request_data.get("mask_feather"),
            "continuation_divergence": request_data.get("continuation_divergence"),
            "brightness_language": request_data.get("brightness_language"),
            "lora_strength": request_data.get("lora_strength"),
            "seed": seed,
            "model": model,
            "provider": provider,
            "duration": request_data.get("duration"),
            "steps": request_data.get("steps"),
            "cfg_scale": request_data.get("cfg_scale"),
            "init_noise_level": request_data.get("init_noise_level"),
            "inpaint_ranges": request_data.get("inpaint_ranges", []),
            "region_roles": request_data.get("region_roles", []),
            "preserve_ranges": request_data.get("preserve_ranges", []),
            "accent_ranges": request_data.get("accent_ranges", []),
            "forbidden_ranges": request_data.get("forbidden_ranges", []),
            "seed_ranges": request_data.get("seed_ranges", []),
            "texture_ranges": request_data.get("texture_ranges", []),
            "variation_ranges": request_data.get("variation_ranges", []),
            "bridge_ranges": request_data.get("bridge_ranges", []),
            "silence_ranges": request_data.get("silence_ranges", []),
            "genetic_identities": request_data.get("genetic_identities", []),
            "generation_sequences": request_data.get("generation_sequences", []),
            "source_duration": request_data.get("source_duration"),
            "target_duration": request_data.get("target_duration"),
            "lora": request_data.get("lora", []),
            "latents": request_data.get("latents") if isinstance(request_data.get("latents"), dict) else {},
            "latent_file": request_data.get("latent_file"),
            "latent_fingerprint": request_data.get("latent_fingerprint"),
            **operation_params,
        }
        operation_params = {
            key: value for key, value in operation_params.items() if value not in (None, "")
        }
        lineage = {
            **raw,
            "id": sound_id,
            "parents": parents,
            "children": children,
            "operation": operation,
            "operation_params": operation_params,
            "prompt": request_data.get("prompt"),
            "seed": seed,
            "model": model,
            "provider": provider,
            "lora_strains": request_data.get("lora", []),
            "latents": request_data.get("latents") if isinstance(request_data.get("latents"), dict) else {},
            "latent_file": request_data.get("latent_file"),
            "latent_fingerprint": request_data.get("latent_fingerprint"),
            "audio_path": self.relative_path(output_audio_path) if output_audio_path else None,
            "metadata_path": self.relative_path(metadata_path),
            "parent_metadata_paths": self._string_list(raw.get("parent_metadata_paths")),
            "parent_branch": raw.get("parent_branch"),
            "region": raw.get("region") or raw.get("source_region"),
        }
        return lineage

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item not in (None, "")]

    @staticmethod
    def _compact_lora_specs(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        specs: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            spec = {
                key: item.get(key)
                for key in (
                    "path",
                    "id",
                    "name",
                    "strength",
                    "tags",
                    "author",
                    "license",
                    "source_dataset",
                    "prompt_vocabulary",
                    "recommended_modules",
                    "provenance_notes",
                    "metadata",
                )
                if item.get(key) not in (None, "", [], {})
            }
            if spec:
                specs.append(spec)
        return specs

    def _append_lineage_child(self, parent_metadata_paths: list[str], child_id: str) -> None:
        with self._lock:
            for parent_metadata_path in parent_metadata_paths:
                try:
                    path = self.resolve_existing_metadata_path(
                        parent_metadata_path,
                        label="parent metadata",
                    )
                    if path.suffix.lower() != ".json":
                        continue
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
                    continue
                children = self._string_list(data.get("children"))
                if child_id not in children:
                    children.append(child_id)
                data["children"] = children
                lineage = data.get("lineage") if isinstance(data.get("lineage"), dict) else {}
                lineage["children"] = children
                data["lineage"] = lineage
                self._write_json_atomic(path, data)

    @staticmethod
    def _germinator_mode(mode: str) -> str:
        return {
            "text-to-audio": "germinate",
            "audio-to-audio": "mutate",
            "inpainting": "prune",
            "continuation": "propagate",
            "time-render": "harvest",
            "audio-time-pitch": "time_pitch",
        }.get(mode, "archive")

    @staticmethod
    def _runtime_from_provider(provider: str) -> str:
        return {
            "stable_audio_python": "python",
            "stable_audio_mlx": "mlx",
            "stability_api": "api",
            "mock": "mock",
        }.get(provider, provider)

    def write_error_metadata(
        self,
        *,
        request: BaseModel,
        mode: str,
        job_id: str,
        error: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> GenerationResult:
        metadata_path = self.reserve_paths(request=request, mode=mode, job_id=job_id)[0][1]
        request_data = request.model_dump(exclude={"job_id"})
        provider = provider or request_data.get("provider", "unknown")
        model = model or request_data.get("model", "unknown")
        self.write_metadata(
            metadata_path=metadata_path,
            request=request,
            mode=mode,
            provider=provider,
            model=model,
            seed=request_data.get("seed"),
            output_audio_path=None,
            sample_rate=None,
            status="error",
            error=error,
        )
        result = GenerationResult(
            job_id=job_id,
            status="error",
            audio_files=[],
            metadata_files=[self.relative_path(metadata_path)],
            seed=request_data.get("seed"),
            duration=request_data.get("duration"),
            sample_rate=None,
            error=error,
            provider=provider,
            model=model,
            mode=mode,
        )
        self.record_result(result)
        return result

    def record_result(self, result: GenerationResult) -> None:
        with self._lock:
            job = self.jobs.get(result.job_id)
            if not job:
                timestamp = utc_now_iso()
                job = {
                    "job_id": result.job_id,
                    "status": result.status,
                    "mode": result.mode or "unknown",
                    "provider": result.provider,
                    "model": result.model,
                    "request": {},
                    "audio_files": [],
                    "metadata_files": [],
                    "error": None,
                    "metrics": {},
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                self.jobs[result.job_id] = job
                self._evict_old_jobs()
            job["status"] = result.status
            job["provider"] = result.provider
            job["model"] = result.model
            job["mode"] = result.mode or job.get("mode", "unknown")
            job["audio_files"] = result.audio_files
            job["metadata_files"] = result.metadata_files
            job["error"] = result.error
            job["updated_at"] = utc_now_iso()

    def get_job(self, job_id: str) -> JobStatus | None:
        with self._lock:
            job = dict(self.jobs[job_id]) if job_id in self.jobs else None
        return JobStatus(**job) if job else None

    def random_seed(self) -> int:
        return secrets.randbelow(2**31 - 1)

    async def save_upload_stream(
        self,
        *,
        filename: str,
        upload: Any,
        max_bytes: int,
        directory: str | Path | None = None,
    ) -> tuple[Path, int]:
        stem = safe_stem(Path(filename).stem, fallback="upload")
        suffix = safe_suffix(Path(filename).suffix)
        target_dir = Path(directory) if directory is not None else self.upload_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{stem}_{uuid4().hex[:8]}{suffix}"
        total = 0
        try:
            with path.open("wb") as handle:
                while True:
                    chunk = await upload.read(UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(
                            f"uploaded file exceeds the {max_bytes // (1024 * 1024)} MB limit"
                        )
                    handle.write(chunk)
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return path, total
