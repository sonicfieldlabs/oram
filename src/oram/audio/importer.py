"""Audio import helpers for loading user files into ORAM layers."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np

from oram.audio.layer import LayerManager
from oram.audio.resample import ensure_stereo_float32
from oram.types import Layer, SourceType

MAX_UPLOAD_BYTES = 100 * 1024 * 1024


def decode_audio_bytes(data: bytes, *, target_sample_rate: int) -> tuple[np.ndarray, int]:
    """Decode user-uploaded audio bytes into stereo float32 at the session rate."""
    if not data:
        raise ValueError("empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("audio upload is larger than 100 MB")

    import soundfile as sf

    try:
        audio, source_sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:
        raise ValueError(f"unsupported or unreadable audio file: {exc}") from exc

    if audio.size == 0 or audio.shape[0] == 0:
        raise ValueError("audio file has no samples")

    normalized = ensure_stereo_float32(audio, int(source_sr), int(target_sample_rate))
    if normalized.shape[0] == 0:
        raise ValueError("audio file has no usable samples")
    return normalized, int(target_sample_rate)


def assign_imported_audio(
    manager: LayerManager,
    layer: Layer,
    audio: np.ndarray,
    *,
    filename: str,
    sample_rate: int,
) -> None:
    """Assign decoded audio to a layer and reset generation metadata."""
    layer.sample_rate = int(sample_rate)
    layer.name = _layer_name_from_filename(filename, fallback=f"layer_{layer.slot + 1}")
    manager.assign_buffer(layer, audio)
    layer.source_type = SourceType.IMPORTED
    layer.is_generated = False
    layer.generation_prompt = None
    layer.parent_layer_id = None
    layer.generation_depth = 0
    layer.engine_provider = ""


def _layer_name_from_filename(filename: str, *, fallback: str) -> str:
    stem = Path(filename or "").stem.strip()
    if not stem:
        return fallback
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe[:48] or fallback
