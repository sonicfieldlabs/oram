from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import wave
from array import array
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from oram_sa3_server.identity import LEGACY_ENGINE_NAME, PRODUCT_NAME, SOUND_MATTER_CONCEPT
from oram_sa3_server.registry import settings, storage
from oram_sa3_server.schemas import GenerateRequest, GenerationResult
from oram_sa3_server.storage import safe_stem, utc_now_iso


router = APIRouter()

AudioToolOperation = Literal[
    "extract_region",
    "normalize",
    "seam_healer",
    "loop_doctor",
    "tail_extender",
    "texture_flatten",
    "silence_cleaner",
    "spectral_freeze",
    "onset_splitter",
    "loudness_match",
    "phase_mono_check",
    "stem_extract_prep",
    "fade",
    "crossfade_loop",
    "reverse",
    "duplicate",
    "slice",
    "metadata",
]

LINEAGE_OPERATION_NAMES = {
    "extract_region": "slice",
    "normalize": "normalize",
    "seam_healer": "seam_healer",
    "loop_doctor": "loop_doctor",
    "tail_extender": "tail_extender",
    "texture_flatten": "texture_flatten",
    "silence_cleaner": "silence_cleaner",
    "spectral_freeze": "spectral_freeze",
    "onset_splitter": "onset_splitter",
    "loudness_match": "loudness_match",
    "phase_mono_check": "phase_mono_check",
    "stem_extract_prep": "stem_extract_prep",
    "fade": "fade",
    "crossfade_loop": "crossfade_loop",
    "reverse": "reverse",
    "duplicate": "duplicate",
    "slice": "slice",
    "metadata": "metadata",
}


class AudioToolRegion(BaseModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    id: str | None = None
    purpose: str | None = None
    region_type: str | None = None
    label: str | None = None
    role: str | None = None
    behavior: str | None = None
    intent: str | None = None
    locked: bool = False

    @field_validator("end_sec")
    @classmethod
    def validate_region(cls, end_sec: float, info: Any) -> float:
        start_sec = info.data.get("start_sec") if hasattr(info, "data") else None
        if start_sec is not None and end_sec <= start_sec:
            raise ValueError("region end_sec must be greater than start_sec")
        return end_sec


class AudioToolRequest(BaseModel):
    input_audio_path: str
    operation: AudioToolOperation
    metadata_path: str | None = None
    output_name: str | None = None
    start_sec: float | None = Field(default=None, ge=0)
    end_sec: float | None = Field(default=None, gt=0)
    regions: list[AudioToolRegion] = Field(default_factory=list)
    region_roles: list[dict[str, Any]] = Field(default_factory=list)
    preserve_ranges: list[tuple[float, float]] = Field(default_factory=list)
    accent_ranges: list[tuple[float, float]] = Field(default_factory=list)
    forbidden_ranges: list[tuple[float, float]] = Field(default_factory=list)
    seed_ranges: list[tuple[float, float]] = Field(default_factory=list)
    texture_ranges: list[tuple[float, float]] = Field(default_factory=list)
    variation_ranges: list[tuple[float, float]] = Field(default_factory=list)
    bridge_ranges: list[tuple[float, float]] = Field(default_factory=list)
    silence_ranges: list[tuple[float, float]] = Field(default_factory=list)
    fade_in_sec: float = Field(default=0.04, ge=0, le=30)
    fade_out_sec: float = Field(default=0.04, ge=0, le=30)
    crossfade_sec: float = Field(default=0.08, ge=0.001, le=30)
    tail_extension_sec: float = Field(default=2.0, ge=0.05, le=60)
    freeze_duration_sec: float = Field(default=8.0, ge=0.1, le=120)
    silence_threshold: float = Field(default=0.012, ge=0.0, le=1.0)
    onset_threshold: float = Field(default=0.32, ge=0.01, le=1.0)
    slice_count: int = Field(default=4, ge=2, le=64)
    prompt: str | None = None
    negative_prompt: str | None = None
    seed: int | None = None
    steps: int | None = None
    cfg_scale: float | None = None
    batch_size: int | None = 1
    lora: list[dict[str, Any]] = Field(default_factory=list)
    culture_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    lineage: dict[str, Any] = Field(default_factory=dict)

    @field_validator("end_sec")
    @classmethod
    def validate_direct_region(cls, end_sec: float | None, info: Any) -> float | None:
        start_sec = info.data.get("start_sec") if hasattr(info, "data") else None
        if start_sec is not None and end_sec is not None and end_sec <= start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        return end_sec


class AudioProcessRequest(BaseModel):
    input_audio_path: str
    metadata_path: str | None = None
    output_name: str | None = None
    pitch_semitones: float = Field(default=0.0, ge=-48.0, le=48.0)
    stretch_ratio: float | None = Field(default=None, ge=0.05, le=20.0)
    target_duration_sec: float | None = Field(default=None, ge=0.01, le=380.0)
    quality: Literal["fast", "fine"] = "fine"
    culture_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    lineage: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_duration_sec")
    @classmethod
    def validate_ratio_or_duration(cls, target_duration_sec: float | None, info: Any) -> float | None:
        stretch_ratio = info.data.get("stretch_ratio") if hasattr(info, "data") else None
        if target_duration_sec is not None and stretch_ratio is not None:
            raise ValueError("Use either stretch_ratio or target_duration_sec, not both")
        return target_duration_sec


class WavPayload(BaseModel):
    channels: int
    sample_width: int
    sample_rate: int
    frame_count: int
    frames: bytes

    @property
    def frame_width(self) -> int:
        return self.channels * self.sample_width

    @property
    def duration(self) -> float:
        return self.frame_count / float(self.sample_rate)


def _resolve_output_audio(path: str) -> Path:
    try:
        target = storage.resolve_existing_path(path, label="audio")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        target.relative_to(settings.output_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Only output audio files can be edited.") from exc
    if target.suffix.lower() != ".wav":
        raise HTTPException(status_code=422, detail="Audio tools currently require WAV source files.")
    return target


def _read_metadata(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        target = storage.resolve_existing_metadata_path(path, label="metadata")
        if target.suffix.lower() != ".json":
            return {}
        return json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return {}


def _read_wav(path: Path) -> WavPayload:
    try:
        with wave.open(str(path), "rb") as wav:
            if wav.getcomptype() != "NONE":
                raise HTTPException(status_code=422, detail="Compressed WAV files are not supported by local audio tools.")
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frame_count = wav.getnframes()
            frames = wav.readframes(frame_count)
    except wave.Error as exc:
        raise HTTPException(status_code=422, detail=f"Invalid WAV file: {exc}") from exc
    if channels <= 0 or sample_rate <= 0 or sample_width <= 0:
        raise HTTPException(status_code=422, detail="Invalid WAV parameters.")
    return WavPayload(
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
        frame_count=frame_count,
        frames=frames,
    )


def _write_wav(path: Path, payload: WavPayload, frames: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = len(frames) // payload.frame_width
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(payload.channels)
        wav.setsampwidth(payload.sample_width)
        wav.setframerate(payload.sample_rate)
        wav.writeframes(frames)
    return frame_count


def _slice_frames(payload: WavPayload, start_sec: float, end_sec: float) -> tuple[bytes, float]:
    start_frame = max(0, min(payload.frame_count - 1, math.floor(start_sec * payload.sample_rate)))
    end_frame = max(start_frame + 1, min(payload.frame_count, math.ceil(end_sec * payload.sample_rate)))
    start_byte = start_frame * payload.frame_width
    end_byte = end_frame * payload.frame_width
    return payload.frames[start_byte:end_byte], (end_frame - start_frame) / float(payload.sample_rate)


def _require_pcm16(payload: WavPayload, operation: str) -> None:
    if payload.sample_width != 2:
        raise HTTPException(status_code=422, detail=f"{operation} requires 16-bit PCM WAV audio.")


def _samples_from_frames(frames: bytes) -> array:
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _frames_from_samples(samples: array) -> bytes:
    output = array("h", samples)
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def _clip_sample(value: float) -> int:
    return max(-32768, min(32767, int(round(value))))


def _normalize_frames(payload: WavPayload) -> bytes:
    _require_pcm16(payload, "Normalize")
    samples = _samples_from_frames(payload.frames)
    peak = max((abs(sample) for sample in samples), default=0)
    if peak <= 0:
        return payload.frames
    gain = (32767 * 0.98) / peak
    return _frames_from_samples(array("h", (_clip_sample(sample * gain) for sample in samples)))


def _fade_frames(payload: WavPayload, fade_in_sec: float, fade_out_sec: float) -> bytes:
    _require_pcm16(payload, "Fade")
    samples = _samples_from_frames(payload.frames)
    channels = payload.channels
    total_frames = len(samples) // channels
    fade_in = min(total_frames, round(fade_in_sec * payload.sample_rate))
    fade_out = min(total_frames, round(fade_out_sec * payload.sample_rate))
    for frame in range(total_frames):
        factor = 1.0
        if fade_in > 0 and frame < fade_in:
            factor = min(factor, frame / float(fade_in))
        if fade_out > 0 and frame >= total_frames - fade_out:
            factor = min(factor, (total_frames - frame - 1) / float(fade_out))
        if factor >= 0.999:
            continue
        for channel in range(channels):
            index = frame * channels + channel
            samples[index] = _clip_sample(samples[index] * factor)
    return _frames_from_samples(samples)


def _crossfade_loop_frames(payload: WavPayload, crossfade_sec: float) -> bytes:
    _require_pcm16(payload, "Crossfade Loop")
    samples = _samples_from_frames(payload.frames)
    channels = payload.channels
    total_frames = len(samples) // channels
    overlap = min(total_frames // 2, max(1, round(crossfade_sec * payload.sample_rate)))
    if overlap <= 0:
        return payload.frames
    output = array("h", samples)
    for frame in range(overlap):
        blend = (frame + 1) / float(overlap + 1)
        start_frame = frame
        end_frame = total_frames - overlap + frame
        for channel in range(channels):
            start_index = start_frame * channels + channel
            end_index = end_frame * channels + channel
            start = samples[start_index]
            end = samples[end_index]
            output[start_index] = _clip_sample(start * (1.0 - blend) + end * blend)
            output[end_index] = _clip_sample(end * (1.0 - blend) + start * blend)
    return _frames_from_samples(output)


def _reverse_frames(payload: WavPayload) -> bytes:
    frame_width = payload.frame_width
    return b"".join(
        payload.frames[index : index + frame_width]
        for index in range(len(payload.frames) - frame_width, -1, -frame_width)
    )


def _payload_with_frames(payload: WavPayload, frames: bytes) -> WavPayload:
    return payload.model_copy(
        update={
            "frames": frames,
            "frame_count": len(frames) // payload.frame_width,
        },
    )


def _mono_safe_frames(payload: WavPayload) -> bytes:
    _require_pcm16(payload, "Phase / Mono Check")
    if payload.channels <= 1:
        return payload.frames
    samples = _samples_from_frames(payload.frames)
    output = array("h", samples)
    total_frames = len(samples) // payload.channels
    for frame in range(total_frames):
        start = frame * payload.channels
        mono = _clip_sample(sum(samples[start + channel] for channel in range(payload.channels)) / payload.channels)
        for channel in range(payload.channels):
            output[start + channel] = mono
    return _frames_from_samples(output)


def _texture_flatten_frames(payload: WavPayload) -> bytes:
    _require_pcm16(payload, "Texture Flatten")
    samples = _samples_from_frames(payload.frames)
    threshold = 32767 * 0.28
    ratio = 0.24
    flattened = array("h")
    for sample in samples:
        sign = -1 if sample < 0 else 1
        magnitude = abs(sample)
        if magnitude > threshold:
            magnitude = threshold + (magnitude - threshold) * ratio
        flattened.append(_clip_sample(sign * magnitude))
    peak = max((abs(sample) for sample in flattened), default=0)
    if peak > 0:
        gain = (32767 * 0.72) / peak
        flattened = array("h", (_clip_sample(sample * gain) for sample in flattened))
    return _frames_from_samples(flattened)


def _tail_extend_frames(payload: WavPayload, extension_sec: float) -> bytes:
    _require_pcm16(payload, "Tail Extender")
    samples = _samples_from_frames(payload.frames)
    channels = payload.channels
    total_frames = len(samples) // channels
    extension_frames = max(1, round(extension_sec * payload.sample_rate))
    tail_frames = min(total_frames, max(1, round(min(1.5, max(0.08, extension_sec * 0.5)) * payload.sample_rate)))
    tail_start = max(0, total_frames - tail_frames)
    output = array("h", samples)
    for frame in range(extension_frames):
        source_frame = tail_start + (frame % tail_frames)
        gain = math.exp(-3.2 * (frame / max(1, extension_frames - 1)))
        for channel in range(channels):
            output.append(_clip_sample(samples[source_frame * channels + channel] * gain))
    return _frames_from_samples(output)


def _trim_silence_frames(payload: WavPayload, threshold: float) -> bytes:
    _require_pcm16(payload, "Silence Cleaner")
    samples = _samples_from_frames(payload.frames)
    channels = payload.channels
    total_frames = len(samples) // channels
    limit = max(0, min(32767, round(32767 * threshold)))

    first = None
    for frame in range(total_frames):
        start = frame * channels
        if max(abs(samples[start + channel]) for channel in range(channels)) > limit:
            first = frame
            break
    if first is None:
        return payload.frames

    last = first
    for frame in range(total_frames - 1, first - 1, -1):
        start = frame * channels
        if max(abs(samples[start + channel]) for channel in range(channels)) > limit:
            last = frame
            break

    pad = round(0.025 * payload.sample_rate)
    first = max(0, first - pad)
    last = min(total_frames - 1, last + pad)
    return payload.frames[first * payload.frame_width : (last + 1) * payload.frame_width]


def _region_frames_or_center(payload: WavPayload, request: AudioToolRequest, default_sec: float = 0.3) -> tuple[bytes, float, float, float]:
    if request.start_sec is not None and request.end_sec is not None:
        start = max(0.0, min(payload.duration, request.start_sec))
        end = max(start + 0.01, min(payload.duration, request.end_sec))
    else:
        span = min(payload.duration, max(0.04, default_sec))
        start = max(0.0, payload.duration * 0.5 - span * 0.5)
        end = min(payload.duration, start + span)
    frames, duration = _slice_frames(payload, start, end)
    return frames, duration, start, end


def _spectral_freeze_frames(payload: WavPayload, request: AudioToolRequest) -> tuple[bytes, float, dict[str, Any]]:
    _require_pcm16(payload, "Spectral Freeze")
    region_frames, region_duration, start, end = _region_frames_or_center(payload, request)
    if not region_frames:
        return payload.frames, payload.duration, {}
    target_frames = max(1, round(request.freeze_duration_sec * payload.sample_rate))
    source_frame_count = max(1, len(region_frames) // payload.frame_width)
    fade_frames = min(target_frames // 3, max(1, round(0.08 * payload.sample_rate)))
    output = bytearray()
    for frame in range(target_frames):
        source_frame = frame % source_frame_count
        start_byte = source_frame * payload.frame_width
        chunk = region_frames[start_byte : start_byte + payload.frame_width]
        if payload.sample_width == 2 and (frame < fade_frames or frame >= target_frames - fade_frames):
            chunk_samples = _samples_from_frames(chunk)
            fade = 1.0
            if frame < fade_frames:
                fade = min(fade, frame / float(fade_frames))
            if frame >= target_frames - fade_frames:
                fade = min(fade, (target_frames - frame - 1) / float(fade_frames))
            chunk = _frames_from_samples(array("h", (_clip_sample(sample * fade) for sample in chunk_samples)))
        output.extend(chunk)
    return bytes(output), target_frames / float(payload.sample_rate), {
        "freeze_source_start_sec": start,
        "freeze_source_end_sec": end,
        "freeze_source_duration": region_duration,
    }


def _onset_regions(payload: WavPayload, threshold: float, max_regions: int = 16) -> list[tuple[float, float]]:
    _require_pcm16(payload, "Onset Splitter")
    samples = _samples_from_frames(payload.frames)
    channels = payload.channels
    window = max(64, round(0.012 * payload.sample_rate))
    hop = max(32, window // 2)
    energies: list[tuple[int, float]] = []
    for frame in range(0, max(1, payload.frame_count - window), hop):
        total = 0.0
        count = 0
        for local_frame in range(window):
            sample_frame = frame + local_frame
            if sample_frame >= payload.frame_count:
                break
            start = sample_frame * channels
            for channel in range(channels):
                sample = samples[start + channel] / 32768.0
                total += sample * sample
                count += 1
        rms = math.sqrt(total / max(1, count))
        energies.append((frame, rms))
    peak = max((energy for _, energy in energies), default=0)
    if peak <= 0:
        return []
    gate = peak * threshold
    min_gap = round(0.08 * payload.sample_rate)
    pre = round(0.015 * payload.sample_rate)
    post = round(0.24 * payload.sample_rate)
    regions: list[tuple[float, float]] = []
    last_start = -min_gap
    was_low = True
    for frame, energy in energies:
        if energy >= gate and was_low and frame - last_start >= min_gap:
            start_frame = max(0, frame - pre)
            end_frame = min(payload.frame_count, frame + post)
            regions.append((start_frame / payload.sample_rate, end_frame / payload.sample_rate))
            last_start = frame
            if len(regions) >= max_regions:
                break
        was_low = energy < gate * 0.55
    if not regions:
        regions.append((0.0, min(payload.duration, max(0.05, payload.duration / 4))))
    return regions


def _target_paths(request: AudioToolRequest, job_id: str, count: int = 1) -> list[tuple[Path, Path]]:
    stem = request.output_name or f"canvas_{request.operation}_{safe_stem(Path(request.input_audio_path).stem, 'sound')}"
    request_for_paths = request.model_copy(update={"output_name": stem})
    return storage.reserve_paths(request=request_for_paths, mode=f"audio-{request.operation}", job_id=job_id, count=count)


def _process_target_paths(request: AudioProcessRequest, job_id: str) -> tuple[Path, Path]:
    stem = request.output_name or f"canvas_time_pitch_{safe_stem(Path(request.input_audio_path).stem, 'sound')}"
    request_for_paths = request.model_copy(update={"output_name": stem})
    return storage.reserve_paths(
        request=request_for_paths,
        mode="audio-time-pitch",
        job_id=job_id,
        extension=".wav",
    )[0]


def _lineage_for(
    request: AudioToolRequest,
    *,
    source_audio: Path,
    source_metadata: dict[str, Any],
    region: dict[str, Any] | None,
    operation_params: dict[str, Any],
) -> dict[str, Any]:
    raw = request.lineage if isinstance(request.lineage, dict) else {}
    raw_params = raw.get("operation_params") if isinstance(raw.get("operation_params"), dict) else {}
    parent_id = (
        source_metadata.get("sound_id")
        or (source_metadata.get("lineage") if isinstance(source_metadata.get("lineage"), dict) else {}).get("id")
        or storage.relative_path(source_audio)
    )
    parent_metadata_paths = raw.get("parent_metadata_paths") if isinstance(raw.get("parent_metadata_paths"), list) else []
    if request.metadata_path and request.metadata_path not in parent_metadata_paths:
        parent_metadata_paths = [*parent_metadata_paths, request.metadata_path]
    parents = raw.get("parents") if isinstance(raw.get("parents"), list) else []
    if parent_id and parent_id not in parents:
        parents = [*parents, parent_id]
    return {
        **raw,
        "parents": parents,
        "parent_metadata_paths": parent_metadata_paths,
        "operation": LINEAGE_OPERATION_NAMES.get(request.operation, request.operation),
        "source_audio_path": storage.relative_path(source_audio),
        "region": region or raw.get("region") or raw.get("source_region"),
        "operation_params": {
            **raw_params,
            **operation_params,
            "source_audio_path": storage.relative_path(source_audio),
        },
    }


def _metadata_request(
    request: AudioToolRequest,
    *,
    source_audio: Path,
    source_metadata: dict[str, Any],
    duration: float,
    region: dict[str, Any] | None,
    operation_params: dict[str, Any],
) -> AudioToolRequest:
    lineage = _lineage_for(
        request,
        source_audio=source_audio,
        source_metadata=source_metadata,
        region=region,
        operation_params=operation_params,
    )
    prompt = request.prompt if request.prompt is not None else source_metadata.get("prompt")
    negative_prompt = request.negative_prompt if request.negative_prompt is not None else source_metadata.get("negative_prompt")
    tags = request.tags or source_metadata.get("tags") or []
    return request.model_copy(
        update={
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "tags": tags,
            "lineage": lineage,
        },
    )


def _write_tool_metadata(
    request: AudioToolRequest,
    *,
    metadata_path: Path,
    audio_path: Path,
    source_audio: Path,
    source_metadata: dict[str, Any],
    duration: float,
    sample_rate: int,
    region: dict[str, Any] | None,
    operation_params: dict[str, Any],
) -> dict[str, Any]:
    metadata_request = _metadata_request(
        request,
        source_audio=source_audio,
        source_metadata=source_metadata,
        duration=duration,
        region=region,
        operation_params=operation_params,
    )
    return storage.write_metadata(
        metadata_path=metadata_path,
        request=metadata_request,
        mode=f"audio-{request.operation}",
        provider="local",
        model="wav-tools",
        seed=request.seed if request.seed is not None else source_metadata.get("seed"),
        output_audio_path=audio_path,
        sample_rate=sample_rate,
        status="done",
        extra={
            "germinator_mode": LINEAGE_OPERATION_NAMES.get(request.operation, request.operation),
            "tool_operation": request.operation,
            "notes": request.notes if request.notes is not None else source_metadata.get("notes"),
        },
    )


def _lineage_for_process(
    request: AudioProcessRequest,
    *,
    source_audio: Path,
    source_metadata: dict[str, Any],
    operation_params: dict[str, Any],
) -> dict[str, Any]:
    raw = request.lineage if isinstance(request.lineage, dict) else {}
    raw_params = raw.get("operation_params") if isinstance(raw.get("operation_params"), dict) else {}
    parent_id = (
        source_metadata.get("sound_id")
        or (source_metadata.get("lineage") if isinstance(source_metadata.get("lineage"), dict) else {}).get("id")
        or storage.relative_path(source_audio)
    )
    parent_metadata_paths = raw.get("parent_metadata_paths") if isinstance(raw.get("parent_metadata_paths"), list) else []
    if request.metadata_path and request.metadata_path not in parent_metadata_paths:
        parent_metadata_paths = [*parent_metadata_paths, request.metadata_path]
    parents = raw.get("parents") if isinstance(raw.get("parents"), list) else []
    if parent_id and parent_id not in parents:
        parents = [*parents, parent_id]
    return {
        **raw,
        "parents": parents,
        "parent_metadata_paths": parent_metadata_paths,
        "operation": "time_pitch_process",
        "source_audio_path": storage.relative_path(source_audio),
        "operation_params": {
            **raw_params,
            **operation_params,
            "source_audio_path": storage.relative_path(source_audio),
        },
    }


def _process_metadata_request(
    request: AudioProcessRequest,
    *,
    source_audio: Path,
    source_metadata: dict[str, Any],
    duration: float,
    operation_params: dict[str, Any],
) -> GenerateRequest:
    lineage = _lineage_for_process(
        request,
        source_audio=source_audio,
        source_metadata=source_metadata,
        operation_params=operation_params,
    )
    return GenerateRequest(
        provider="mock",
        model="rubberband",
        prompt=str(source_metadata.get("prompt") or "Time/pitch processed source"),
        negative_prompt=str(source_metadata.get("negative_prompt") or ""),
        duration=duration,
        seed=int(source_metadata.get("seed") or -1),
        batch_size=1,
        output_name=request.output_name,
        culture_id=request.culture_id or source_metadata.get("culture_id"),
        tags=request.tags or source_metadata.get("tags") or [],
        notes=request.notes if request.notes is not None else source_metadata.get("notes"),
        lineage=lineage,
    )


def _rubberband_binary() -> str | None:
    return shutil.which("rubberband-r3") or shutil.which("rubberband")


def _rubberband_command(
    binary: str,
    request: AudioProcessRequest,
    source_audio: Path,
    output_audio: Path,
    ratio: float,
) -> list[str]:
    command = [
        binary,
        "-t",
        f"{ratio:.8f}",
        "-p",
        f"{request.pitch_semitones:.8f}",
    ]
    command.append("-3" if request.quality == "fine" else "-2")
    command.extend([str(source_audio), str(output_audio)])
    return command


def _update_existing_metadata(request: AudioToolRequest, source_audio: Path, source_metadata: dict[str, Any]) -> Path:
    if request.metadata_path:
        try:
            metadata_path = storage.resolve_existing_metadata_path(
                request.metadata_path,
                label="metadata",
            )
            if metadata_path.suffix.lower() != ".json":
                raise FileNotFoundError
        except (FileNotFoundError, PermissionError) as exc:
            raise HTTPException(status_code=404, detail=f"Metadata not found: {request.metadata_path}") from exc
        data = source_metadata or {}
    else:
        job_id = storage.new_job("audio-metadata", request.model_dump())
        metadata_path = _target_paths(request, job_id, 1)[0][1]
        data = {
            "app": PRODUCT_NAME,
            "product": PRODUCT_NAME,
            "legacy_app": LEGACY_ENGINE_NAME,
            "concept": SOUND_MATTER_CONCEPT,
            "engine": settings.engine_name,
            "provider": "local",
            "runtime": "audio-tool",
            "model": "wav-tools",
            "mode": "audio-metadata",
            "technical_mode": "audio-metadata",
            "germinator_mode": "archive",
            "output_audio_path": storage.relative_path(source_audio),
            "absolute_output_audio_path": storage.absolute_path(source_audio),
            "metadata_path": storage.relative_path(metadata_path),
            "absolute_metadata_path": storage.absolute_path(metadata_path),
            "created_at": utc_now_iso(),
            "status": "done",
            "sound_id": f"sound_{metadata_path.stem}",
            "parents": [],
            "children": [],
            "operation": "metadata",
            "operation_params": {},
            "lineage": {
                "id": f"sound_{metadata_path.stem}",
                "parents": [],
                "children": [],
                "operation": "metadata",
                "operation_params": {},
                "audio_path": storage.relative_path(source_audio),
                "metadata_path": storage.relative_path(metadata_path),
            },
        }

    if request.prompt is not None:
        data["prompt"] = request.prompt
    if request.negative_prompt is not None:
        data["negative_prompt"] = request.negative_prompt
    if request.tags:
        data["tags"] = request.tags
    if request.notes is not None:
        data["notes"] = request.notes
    data["updated_at"] = utc_now_iso()
    data["tool_operation"] = "metadata"
    operation_params = data.get("operation_params") if isinstance(data.get("operation_params"), dict) else {}
    request_params = request.lineage.get("operation_params") if isinstance(request.lineage, dict) and isinstance(request.lineage.get("operation_params"), dict) else {}
    operation_params.update(request_params)
    operation_params["metadata_edited_at"] = data["updated_at"]
    data["operation_params"] = operation_params
    lineage = data.get("lineage") if isinstance(data.get("lineage"), dict) else {}
    lineage_params = lineage.get("operation_params") if isinstance(lineage.get("operation_params"), dict) else {}
    lineage_params.update(request_params)
    lineage_params["metadata_edited_at"] = data["updated_at"]
    lineage["operation_params"] = lineage_params
    data["lineage"] = lineage
    storage.write_json_atomic(metadata_path, data, touch_library=True)
    return metadata_path


@router.post("/audio/process", response_model=GenerationResult)
def process_audio(request: AudioProcessRequest) -> GenerationResult:
    try:
        source_audio = _resolve_output_audio(request.input_audio_path)
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            raise HTTPException(status_code=422, detail=exc.detail) from exc
        raise
    source_metadata = _read_metadata(request.metadata_path)
    payload = _read_wav(source_audio)
    binary = _rubberband_binary()
    if not binary:
        raise HTTPException(
            status_code=422,
            detail="Rubber Band is not installed. Install the `rubberband` or `rubberband-r3` command-line tool to use time-stretch/pitch-shift processing.",
        )

    ratio = request.stretch_ratio
    if ratio is None:
        ratio = request.target_duration_sec / payload.duration if (request.target_duration_sec and payload.duration) else 1.0
    ratio = max(0.05, min(20.0, float(ratio)))
    expected_duration = payload.duration * ratio
    job_id = storage.new_job("audio-time-pitch", request.model_dump())
    audio_path, metadata_path = _process_target_paths(request, job_id)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = _rubberband_command(binary, request, source_audio, audio_path, ratio)
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Rubber Band processing timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise HTTPException(status_code=422, detail=f"Rubber Band processing failed: {detail}") from exc

    output_payload = _read_wav(audio_path)
    operation_params = {
        "operation": "time_pitch_process",
        "engine": "rubberband",
        "binary": Path(binary).name,
        "quality": request.quality,
        "pitch_semitones": request.pitch_semitones,
        "stretch_ratio": ratio,
        "source_duration": payload.duration,
        "output_duration": output_payload.duration,
        "requested_target_duration": request.target_duration_sec,
    }
    metadata_request = _process_metadata_request(
        request,
        source_audio=source_audio,
        source_metadata=source_metadata,
        duration=output_payload.duration or expected_duration,
        operation_params=operation_params,
    )
    metadata_request = metadata_request.model_copy(update={"job_id": job_id})
    storage.write_metadata(
        metadata_path=metadata_path,
        request=metadata_request,
        mode="audio-time-pitch",
        provider="local",
        model="rubberband",
        seed=source_metadata.get("seed") if source_metadata.get("seed") is not None else -1,
        output_audio_path=audio_path,
        sample_rate=output_payload.sample_rate,
        status="done",
        extra={
            "germinator_mode": "time_pitch",
            "tool_operation": "time_pitch_process",
            "source_type": "time_pitch",
            "time_pitch": operation_params,
        },
    )
    result = GenerationResult(
        job_id=job_id,
        status="done",
        audio_files=[storage.relative_path(audio_path)],
        metadata_files=[storage.relative_path(metadata_path)],
        duration=output_payload.duration,
        sample_rate=output_payload.sample_rate,
        provider="local",
        model="rubberband",
        mode="audio-time-pitch",
    )
    storage.record_result(result)
    return result


@router.post("/audio-tools/operate", response_model=GenerationResult)
def operate_audio(request: AudioToolRequest) -> GenerationResult:
    source_audio = _resolve_output_audio(request.input_audio_path)
    source_metadata = _read_metadata(request.metadata_path)

    if request.operation == "metadata":
        metadata_path = _update_existing_metadata(request, source_audio, source_metadata)
        result = GenerationResult(
            job_id="metadata-edit",
            status="done",
            audio_files=[storage.relative_path(source_audio)],
            metadata_files=[storage.relative_path(metadata_path)],
            provider="local",
            model="wav-tools",
            mode="audio-metadata",
        )
        storage.record_result(result)
        return result

    payload = _read_wav(source_audio)
    job_id = storage.new_job("audio-tool", request.model_dump())

    outputs: list[tuple[bytes, float, dict[str, Any] | None, dict[str, Any]]] = []
    if request.operation == "extract_region":
        if request.start_sec is None or request.end_sec is None:
            raise HTTPException(status_code=422, detail="extract_region requires start_sec and end_sec.")
        frames, duration = _slice_frames(payload, request.start_sec, request.end_sec)
        lineage_region = request.lineage.get("region") if isinstance(request.lineage, dict) and isinstance(request.lineage.get("region"), dict) else {}
        region = {
            **lineage_region,
            "purpose": "extract",
            "start_sec": round(request.start_sec, 3),
            "end_sec": round(request.end_sec, 3),
        }
        outputs.append((frames, duration, region, {"start_sec": request.start_sec, "end_sec": request.end_sec}))
    elif request.operation == "slice":
        if request.regions:
            for index, region_spec in enumerate(request.regions, start=1):
                frames, duration = _slice_frames(payload, region_spec.start_sec, region_spec.end_sec)
                region = {
                    "id": region_spec.id,
                    "purpose": region_spec.purpose or "slice",
                    "region_type": region_spec.region_type,
                    "label": region_spec.label,
                    "role": region_spec.role,
                    "behavior": region_spec.behavior,
                    "intent": region_spec.intent,
                    "locked": region_spec.locked,
                    "start_sec": round(region_spec.start_sec, 3),
                    "end_sec": round(region_spec.end_sec, 3),
                }
                outputs.append((frames, duration, region, {"slice_index": index, "start_sec": region_spec.start_sec, "end_sec": region_spec.end_sec}))
        else:
            for index in range(request.slice_count):
                start = (payload.duration / request.slice_count) * index
                end = (payload.duration / request.slice_count) * (index + 1)
                frames, duration = _slice_frames(payload, start, end)
                region = {"purpose": "slice", "start_sec": round(start, 3), "end_sec": round(end, 3)}
                outputs.append((frames, duration, region, {"slice_index": index + 1, "slice_count": request.slice_count, "start_sec": start, "end_sec": end}))
    elif request.operation in {"normalize", "loudness_match"}:
        outputs.append((_normalize_frames(payload), payload.duration, None, {"target_peak": 0.98}))
    elif request.operation == "seam_healer":
        healed = _crossfade_loop_frames(payload, request.crossfade_sec)
        healed_payload = _payload_with_frames(payload, healed)
        outputs.append((_fade_frames(healed_payload, request.fade_in_sec, request.fade_out_sec), healed_payload.duration, None, {"crossfade_sec": request.crossfade_sec, "fade_in_sec": request.fade_in_sec, "fade_out_sec": request.fade_out_sec}))
    elif request.operation == "loop_doctor":
        repaired = _crossfade_loop_frames(payload, request.crossfade_sec)
        repaired_payload = _payload_with_frames(payload, repaired)
        outputs.append((_fade_frames(repaired_payload, request.fade_in_sec, request.fade_out_sec), repaired_payload.duration, None, {"crossfade_sec": request.crossfade_sec, "fade_in_sec": request.fade_in_sec, "fade_out_sec": request.fade_out_sec, "loop_repair": True}))
    elif request.operation == "tail_extender":
        frames = _tail_extend_frames(payload, request.tail_extension_sec)
        duration = len(frames) // payload.frame_width / float(payload.sample_rate)
        outputs.append((frames, duration, None, {"tail_extension_sec": request.tail_extension_sec}))
    elif request.operation == "texture_flatten":
        outputs.append((_texture_flatten_frames(payload), payload.duration, None, {"compression": "soft", "target_peak": 0.72}))
    elif request.operation == "silence_cleaner":
        frames = _trim_silence_frames(payload, request.silence_threshold)
        duration = len(frames) // payload.frame_width / float(payload.sample_rate)
        outputs.append((frames, duration, None, {"silence_threshold": request.silence_threshold}))
    elif request.operation == "spectral_freeze":
        frames, duration, params = _spectral_freeze_frames(payload, request)
        region = {
            "purpose": "texture",
            "region_type": "texture",
            "intent": "spectral freeze",
            "start_sec": round(params.get("freeze_source_start_sec", request.start_sec or 0), 3),
            "end_sec": round(params.get("freeze_source_end_sec", request.end_sec or min(payload.duration, 0.3)), 3),
        }
        outputs.append((frames, duration, region, {"freeze_duration_sec": request.freeze_duration_sec, **params}))
    elif request.operation == "onset_splitter":
        regions = _onset_regions(payload, request.onset_threshold, max_regions=request.slice_count)
        for index, (start, end) in enumerate(regions, start=1):
            frames, duration = _slice_frames(payload, start, end)
            region = {"purpose": "onset", "region_type": "accent", "intent": "split onset", "start_sec": round(start, 3), "end_sec": round(end, 3)}
            outputs.append((frames, duration, region, {"slice_index": index, "slice_count": len(regions), "start_sec": start, "end_sec": end, "onset_threshold": request.onset_threshold}))
    elif request.operation == "phase_mono_check":
        outputs.append((_mono_safe_frames(payload), payload.duration, None, {"mono_safe": True}))
    elif request.operation == "stem_extract_prep":
        frames = _trim_silence_frames(payload, request.silence_threshold)
        work_payload = _payload_with_frames(payload, frames)
        frames = _mono_safe_frames(work_payload)
        work_payload = _payload_with_frames(payload, frames)
        frames = _normalize_frames(work_payload)
        work_payload = _payload_with_frames(payload, frames)
        frames = _fade_frames(work_payload, request.fade_in_sec, request.fade_out_sec)
        duration = len(frames) // payload.frame_width / float(payload.sample_rate)
        outputs.append((frames, duration, None, {"stem_extract_prep": True, "silence_threshold": request.silence_threshold, "fade_in_sec": request.fade_in_sec, "fade_out_sec": request.fade_out_sec}))
    elif request.operation == "fade":
        outputs.append((_fade_frames(payload, request.fade_in_sec, request.fade_out_sec), payload.duration, None, {"fade_in_sec": request.fade_in_sec, "fade_out_sec": request.fade_out_sec}))
    elif request.operation == "crossfade_loop":
        outputs.append((_crossfade_loop_frames(payload, request.crossfade_sec), payload.duration, None, {"crossfade_sec": request.crossfade_sec}))
    elif request.operation == "reverse":
        outputs.append((_reverse_frames(payload), payload.duration, None, {}))
    elif request.operation == "duplicate":
        outputs.append((payload.frames, payload.duration, None, {}))

    if not outputs:
        raise HTTPException(status_code=422, detail=f"Unsupported audio tool operation: {request.operation}")

    paths = _target_paths(request, job_id, len(outputs))
    audio_files: list[str] = []
    metadata_files: list[str] = []
    for index, ((frames, duration, region, operation_params), (audio_path, metadata_path)) in enumerate(zip(outputs, paths), start=1):
        if request.operation == "duplicate":
            shutil.copyfile(source_audio, audio_path)
        else:
            frame_count = _write_wav(audio_path, payload, frames)
            duration = frame_count / float(payload.sample_rate)
        params = {"operation": request.operation, **operation_params}
        if len(outputs) > 1:
            params.setdefault("slice_index", index)
            params.setdefault("slice_count", len(outputs))
        _write_tool_metadata(
            request,
            metadata_path=metadata_path,
            audio_path=audio_path,
            source_audio=source_audio,
            source_metadata=source_metadata,
            duration=duration,
            sample_rate=payload.sample_rate,
            region=region,
            operation_params=params,
        )
        audio_files.append(storage.relative_path(audio_path))
        metadata_files.append(storage.relative_path(metadata_path))

    result = GenerationResult(
        job_id=job_id,
        status="done",
        audio_files=audio_files,
        metadata_files=metadata_files,
        duration=outputs[0][1] if len(outputs) == 1 else None,
        sample_rate=payload.sample_rate,
        provider="local",
        model="wav-tools",
        mode=f"audio-{request.operation}",
    )
    storage.record_result(result)
    return result
