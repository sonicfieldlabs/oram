from __future__ import annotations

import json
import logging
import platform
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from oram_sa3_server.registry import storage

router = APIRouter()
logger = logging.getLogger(__name__)

# Only media files are served over the unauthenticated GET file endpoint. This
# keeps the file server from handing metadata JSON (prompts/lineage) or any other
# non-media file under output/ to arbitrary local browser origins. The mutating
# endpoints (rename/delete) still operate on .json via _resolve_output_file.
SERVABLE_EXTENSIONS = {
    ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
}


class RevealRequest(BaseModel):
    path: str


class RenameRequest(BaseModel):
    audio_path: str
    metadata_path: str | None = None
    new_stem: str


class DeleteRequest(BaseModel):
    audio_path: str
    metadata_path: str | None = None


class BulkDeleteRequest(BaseModel):
    items: list[DeleteRequest]


def sanitize_filename_stem(stem: str) -> str:
    # Allow alphanumeric, underscore, hyphen, space
    sanitized = re.sub(r"[^a-zA-Z0-9_\- ]", "_", stem)
    sanitized = sanitized.strip()
    if not sanitized:
        sanitized = "unnamed"
    return sanitized


def _resolve_output_file(file_path: str) -> Path:
    try:
        return storage.resolve_existing_output_path(file_path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Only output files can be accessed.") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc


@router.post("/files/reveal")
def reveal_output_file(request: RevealRequest) -> dict[str, str]:
    target = _resolve_output_file(request.path)
    if platform.system() != "Darwin":
        raise HTTPException(status_code=400, detail="Reveal is only supported on macOS.")
    subprocess.Popen(["open", "-R", str(target)])
    return {"status": "ok", "path": str(target)}


@router.get("/files/{file_path:path}")
def serve_output_file(file_path: str) -> FileResponse:
    target = _resolve_output_file(file_path)
    if target.suffix.lower() not in SERVABLE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(target)


@router.post("/files/rename")
def rename_output_file(request: RenameRequest) -> dict[str, str]:
    audio_path = _resolve_output_file(request.audio_path)
    metadata_path = None
    if request.metadata_path:
        try:
            metadata_path = _resolve_output_file(request.metadata_path)
        except HTTPException:
            pass

    sanitized_stem = sanitize_filename_stem(request.new_stem)
    if not sanitized_stem:
        raise HTTPException(status_code=400, detail="Invalid filename stem.")

    new_audio_path = audio_path.parent / f"{sanitized_stem}{audio_path.suffix}"
    if new_audio_path.exists() and new_audio_path.resolve() != audio_path.resolve():
        raise HTTPException(status_code=400, detail=f"Target file already exists: {new_audio_path.name}")

    new_metadata_path_str = ""
    metadata_data: dict | None = None
    new_metadata_path = None
    if metadata_path:
        new_metadata_path = metadata_path.parent / f"{sanitized_stem}{metadata_path.suffix}"
        if new_metadata_path.exists() and new_metadata_path.resolve() != metadata_path.resolve():
            raise HTTPException(status_code=400, detail=f"Target metadata file already exists: {new_metadata_path.name}")
        try:
            metadata_data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="Metadata is not valid JSON.") from exc
        except OSError as exc:
            logger.exception("failed to read metadata during output rename")
            raise HTTPException(status_code=500, detail="Failed to read metadata file.") from exc

    try:
        audio_path.rename(new_audio_path)
    except OSError as exc:
        logger.exception("failed to rename output audio")
        raise HTTPException(status_code=500, detail="Failed to rename audio file.") from exc

    if metadata_path and new_metadata_path and metadata_data is not None:
        try:
            metadata_path.rename(new_metadata_path)
        except OSError as exc:
            try:
                new_audio_path.rename(audio_path)
            except OSError:
                pass
            logger.exception("failed to rename output metadata")
            raise HTTPException(status_code=500, detail="Failed to rename metadata file.") from exc

        try:
            new_metadata_path_str = storage.relative_path(new_metadata_path)
            new_audio_path_str = storage.relative_path(new_audio_path)

            data = metadata_data
            data["output_audio_path"] = new_audio_path_str
            data["absolute_output_audio_path"] = storage.absolute_path(new_audio_path)
            data["metadata_path"] = new_metadata_path_str
            data["absolute_metadata_path"] = storage.absolute_path(new_metadata_path)
            if isinstance(data.get("audio"), dict):
                data["audio"]["path"] = new_audio_path_str
                data["audio"]["absolute_path"] = storage.absolute_path(new_audio_path)
            if isinstance(data.get("waveform_preview"), dict) and "audio_path" in data["waveform_preview"]:
                data["waveform_preview"]["audio_path"] = new_audio_path_str

            old_stem = audio_path.stem
            if "sound_id" in data:
                if data["sound_id"] == f"sound_{old_stem}":
                    data["sound_id"] = f"sound_{sanitized_stem}"
                elif data["sound_id"] == old_stem:
                    data["sound_id"] = sanitized_stem

            if isinstance(data.get("lineage"), dict):
                lineage = data["lineage"]
                if lineage.get("id") == f"sound_{old_stem}":
                    lineage["id"] = f"sound_{sanitized_stem}"
                elif lineage.get("id") == old_stem:
                    lineage["id"] = sanitized_stem
                lineage["audio_path"] = new_audio_path_str
                lineage["metadata_path"] = new_metadata_path_str

            storage.write_json_atomic(new_metadata_path, data, touch_library=False)
        except Exception as exc:
            try:
                new_metadata_path.rename(metadata_path)
                new_audio_path.rename(audio_path)
            except OSError:
                pass
            logger.exception("failed to update renamed output metadata")
            raise HTTPException(status_code=500, detail="Failed to update metadata file.") from exc

    storage.touch_library()

    return {
        "status": "ok",
        "audio_path": storage.relative_path(new_audio_path),
        "metadata_path": new_metadata_path_str,
    }


@router.post("/files/delete")
def delete_output_files(request: BulkDeleteRequest) -> dict[str, str]:
    deleted_count = 0
    for item in request.items:
        try:
            audio_path = _resolve_output_file(item.audio_path)
            if audio_path.exists() and audio_path.is_file():
                audio_path.unlink()
                deleted_count += 1
        except HTTPException:
            pass

        if item.metadata_path:
            try:
                metadata_path = _resolve_output_file(item.metadata_path)
                if metadata_path.exists() and metadata_path.is_file():
                    metadata_path.unlink()
            except HTTPException:
                pass

    storage.touch_library()
    return {"status": "ok", "deleted_count": str(deleted_count)}
