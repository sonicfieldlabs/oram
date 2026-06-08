from __future__ import annotations

import array
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, MutableSequence, Sequence

from fastapi import APIRouter, HTTPException

from oram_sa3_server.registry import settings, storage
from oram_sa3_server.schemas import GenerationResult, TimeClock, TimeRenderRequest, TimeRenderSource


router = APIRouter()

TIME_RENDER_MODE = "time-render"
TIME_RENDER_PROVIDER = "local"
TIME_RENDER_MODEL = "time-renderer"
TARGET_PEAK = 32767.0 * 0.98
MAX_TIME_RENDER_SECONDS = 380.0


@dataclass(frozen=True)
class WavPayload:
    samples: array.array
    sample_rate: int
    channels: int
    frames: int
    source: TimeRenderSource
    path: Path


def time_clock_summary(clock: TimeClock) -> dict[str, Any]:
    total_beats = clock.total_beats()
    loop_seconds = clock.loop_seconds()
    return {
        "enabled": clock.enabled,
        "bpm": clock.bpm,
        "beats_per_bar": clock.beats_per_bar,
        "beat_unit": clock.beat_unit,
        "time_signature": f"{clock.beats_per_bar}/{clock.beat_unit}",
        "bars": clock.bars,
        "ppq": clock.ppq,
        "sample_rate": clock.sample_rate,
        "snap_division": clock.snap_division,
        "swing": clock.swing,
        "seconds_per_beat": clock.seconds_per_beat(),
        "total_beats": total_beats,
        "loop_seconds": loop_seconds,
        "loop_samples": clock.loop_samples(),
        "ticks_per_bar": clock.ticks_per_bar(),
        "total_ticks": clock.total_ticks(),
        "loop_start_tick": clock.loop_start_tick,
        "loop_end_tick": clock.resolved_loop_end_tick(),
    }


def _clip_sample(value: float) -> int:
    return max(-32768, min(32767, int(round(value))))


def _ensure_output_path(path: Path, label: str) -> None:
    try:
        path.relative_to(settings.output_root.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{label} must be inside the germ output directory",
        ) from exc


def _resolve_render_source(source: TimeRenderSource) -> Path:
    try:
        path = storage.resolve_existing_path(source.audio_path, label="time render source")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _ensure_output_path(path, "time render source")
    if path.suffix.lower() != ".wav":
        raise HTTPException(status_code=422, detail="time render source must be a WAV file")
    return path


def _read_wav_payload(source: TimeRenderSource, path: Path, clock: TimeClock, label: str) -> WavPayload:
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
            compression = wav.getcomptype()
            raw = wav.readframes(frames)
    except wave.Error as exc:
        raise HTTPException(status_code=422, detail=f"invalid WAV source: {label}") from exc

    if compression != "NONE":
        raise HTTPException(status_code=422, detail=f"compressed WAV sources are not supported: {label}")
    if sample_width != 2:
        raise HTTPException(status_code=422, detail=f"only 16-bit PCM WAV sources are supported: {label}")
    if sample_rate != clock.sample_rate:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{label} sample rate {sample_rate} does not match clock sample rate "
                f"{clock.sample_rate}"
            ),
        )
    if channels not in {1, 2}:
        raise HTTPException(status_code=422, detail=f"only mono or stereo WAV sources are supported: {label}")

    samples = array.array("h")
    samples.frombytes(raw)
    if len(samples) != frames * channels:
        raise HTTPException(status_code=422, detail=f"WAV source has invalid sample data: {label}")
    return WavPayload(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
        source=source,
        path=path,
    )


def _read_source_wav(source: TimeRenderSource, clock: TimeClock) -> WavPayload:
    path = _resolve_render_source(source)
    return _read_wav_payload(source, path, clock, source.id)


def _event_start_frame(tick: int, clock: TimeClock) -> int:
    seconds = (tick / clock.ppq) * clock.seconds_per_beat()
    return int(round(seconds * clock.sample_rate))


def _gain_for_pan(gain: float, pan: float) -> tuple[float, float]:
    pan = max(-1.0, min(1.0, pan))
    left = gain * (1.0 - max(0.0, pan))
    right = gain * (1.0 + min(0.0, pan))
    return left, right


def _mix_event(
    *,
    output: MutableSequence[float],
    output_frames: int,
    payload: WavPayload,
    start_frame: int,
    gain: float,
    pan: float,
    reverse: bool,
    pitch_semitones: float,
    source_start_sec: float | None,
    source_end_sec: float | None,
    duration_frames: int | None,
    fade_in_ms: float,
    fade_out_ms: float,
) -> None:
    if start_frame >= output_frames:
        return
    left_gain, right_gain = _gain_for_pan(gain, pan)
    rate = 2 ** (pitch_semitones / 12.0)
    source_start = int(round((source_start_sec or 0.0) * payload.sample_rate))
    source_end = (
        int(round(source_end_sec * payload.sample_rate))
        if source_end_sec is not None
        else payload.frames
    )
    source_start = max(0, min(payload.frames, source_start))
    source_end = max(source_start, min(payload.frames, source_end))
    source_span = source_end - source_start
    if source_span <= 0:
        return
    available_output_frames = math.ceil(source_span / max(rate, 0.001))
    if duration_frames is not None:
        available_output_frames = min(available_output_frames, max(0, duration_frames))
    frames_to_mix = min(available_output_frames, output_frames - max(0, start_frame))
    if frames_to_mix <= 0:
        return
    fade_in_frames = min(frames_to_mix // 2, int(round((fade_in_ms / 1000.0) * payload.sample_rate)))
    fade_out_frames = min(frames_to_mix // 2, int(round((fade_out_ms / 1000.0) * payload.sample_rate)))

    for local_frame in range(frames_to_mix):
        offset = int(math.floor(local_frame * rate))
        if offset >= source_span:
            break
        source_frame = source_end - 1 - offset if reverse else source_start + offset
        source_index = source_frame * payload.channels
        output_index = (start_frame + local_frame) * 2
        if output_index < 0:
            continue
        if payload.channels == 1:
            left_sample = right_sample = payload.samples[source_index]
        else:
            left_sample = payload.samples[source_index]
            right_sample = payload.samples[source_index + 1]
        envelope = 1.0
        if fade_in_frames > 0 and local_frame < fade_in_frames:
            envelope *= local_frame / float(fade_in_frames)
        if fade_out_frames > 0 and local_frame >= frames_to_mix - fade_out_frames:
            envelope *= (frames_to_mix - local_frame - 1) / float(fade_out_frames)
        output[output_index] += left_sample * left_gain * envelope
        output[output_index + 1] += right_sample * right_gain * envelope


def _frames_to_bytes(samples: Sequence[float], *, normalize: bool) -> bytes:
    peak = max((abs(value) for value in samples), default=0.0)
    scale = TARGET_PEAK / peak if normalize and peak > 0 else 1.0
    output = array.array("h", (_clip_sample(value * scale) for value in samples))
    return output.tobytes()


def _duration_ticks_to_frames(duration_ticks: int | None, clock: TimeClock) -> int | None:
    if duration_ticks is None:
        return None
    seconds = (duration_ticks / clock.ppq) * clock.seconds_per_beat()
    return max(1, int(round(seconds * clock.sample_rate)))


def _rubberband_binary() -> str | None:
    return shutil.which("rubberband-r3") or shutil.which("rubberband")


def _rubberband_pitch_command(binary: str, source: Path, output: Path, pitch_semitones: float) -> list[str]:
    return [
        binary,
        "-p",
        f"{pitch_semitones:.8f}",
        "-t",
        "1",
        "-3",
        str(source),
        str(output),
    ]


def _pitch_shift_payload(
    *,
    payload: WavPayload,
    clock: TimeClock,
    pitch_semitones: float,
    binary: str,
    temp_dir: Path,
) -> WavPayload:
    cache_key = f"{payload.source.id}:{payload.path}:{pitch_semitones:.8f}"
    output_path = temp_dir / f"pitch_{hashlib.sha1(cache_key.encode('utf-8')).hexdigest()}.wav"
    command = _rubberband_pitch_command(binary, payload.path, output_path, pitch_semitones)
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Rubber Band pitch processing timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise HTTPException(status_code=422, detail=f"Rubber Band pitch processing failed: {detail}") from exc
    return _read_wav_payload(
        payload.source,
        output_path,
        clock,
        f"{payload.source.id} pitch {pitch_semitones:+.2f} st",
    )


def _read_parent_id(source: TimeRenderSource) -> str:
    if not source.metadata_path:
        return source.id
    try:
        path = storage.resolve_existing_metadata_path(
            source.metadata_path,
            label="parent metadata",
        )
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return source.id
    lineage = data.get("lineage") if isinstance(data.get("lineage"), dict) else {}
    return str(data.get("sound_id") or lineage.get("id") or source.id)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parent_metadata_paths(sources: list[TimeRenderSource]) -> list[str]:
    paths: list[str] = []
    for source in sources:
        if not source.metadata_path:
            continue
        try:
            path = storage.resolve_existing_metadata_path(
                source.metadata_path,
                label="parent metadata",
            )
        except (FileNotFoundError, PermissionError):
            continue
        if path.suffix.lower() == ".json":
            paths.append(storage.relative_path(path))
    return paths


def _source_manifest(payloads: dict[str, WavPayload]) -> list[dict[str, Any]]:
    return [
        {
            "id": payload.source.id,
            "audio_path": storage.relative_path(payload.path),
            "metadata_path": payload.source.metadata_path,
            "label": payload.source.label,
            "gain": payload.source.gain,
            "pan": payload.source.pan,
            "frames": payload.frames,
            "sample_rate": payload.sample_rate,
            "channels": payload.channels,
            "sha256": _sha256_file(payload.path),
        }
        for payload in payloads.values()
    ]


@router.post("/time/render", response_model=GenerationResult)
def render_time(request: TimeRenderRequest) -> GenerationResult:
    if not request.events:
        raise HTTPException(status_code=422, detail="time render requires at least one event")
    # Schema pins clock.sample_rate to 44100 (Literal) to match the Stable Audio 3
    # SAME encoder/decoder; no runtime re-check needed.

    clock_summary = time_clock_summary(request.clock)
    if clock_summary["loop_seconds"] > MAX_TIME_RENDER_SECONDS:
        raise HTTPException(
            status_code=422,
            detail=f"time render loop length must be {MAX_TIME_RENDER_SECONDS:g} seconds or less",
        )
    output_frames = int(clock_summary["loop_samples"])
    if output_frames <= 0:
        raise HTTPException(status_code=422, detail="time render loop length must be positive")

    payloads = {source.id: _read_source_wav(source, request.clock) for source in request.sources}
    missing_ids = sorted({event.source_id for event in request.events} - set(payloads))
    if missing_ids:
        raise HTTPException(
            status_code=422,
            detail=f"time render event references missing source: {', '.join(missing_ids)}",
        )

    mix = array.array("f", [0.0]) * (output_frames * 2)
    pitch_binary = _rubberband_binary()
    pitch_engine = "none"
    pitch_event_count = sum(1 for event in request.events if abs(event.pitch_semitones) > 1e-6)
    pitch_payload_cache: dict[tuple[str, float], WavPayload] = {}
    with tempfile.TemporaryDirectory(prefix="germinator_time_pitch_") as temp_root:
        temp_dir = Path(temp_root)
        for event in request.events:
            payload = payloads[event.source_id]
            event_pitch = event.pitch_semitones
            if abs(event_pitch) > 1e-6:
                if pitch_binary:
                    pitch_engine = "rubberband"
                    cache_key = (event.source_id, round(event_pitch, 6))
                    if cache_key not in pitch_payload_cache:
                        pitch_payload_cache[cache_key] = _pitch_shift_payload(
                            payload=payload,
                            clock=request.clock,
                            pitch_semitones=event_pitch,
                            binary=pitch_binary,
                            temp_dir=temp_dir,
                        )
                    payload = pitch_payload_cache[cache_key]
                    event_pitch = 0.0
                elif pitch_engine == "none":
                    pitch_engine = "resample_fallback"
            start_frame = _event_start_frame(event.tick, request.clock)
            event_gain = event.velocity * event.gain * payload.source.gain
            event_pan = max(-1.0, min(1.0, payload.source.pan + event.pan))
            _mix_event(
                output=mix,
                output_frames=output_frames,
                payload=payload,
                start_frame=start_frame,
                gain=event_gain,
                pan=event_pan,
                reverse=event.reverse,
                pitch_semitones=event_pitch,
                source_start_sec=event.source_start_sec,
                source_end_sec=event.source_end_sec,
                duration_frames=_duration_ticks_to_frames(event.duration_ticks, request.clock),
                fade_in_ms=event.fade_in_ms,
                fade_out_ms=event.fade_out_ms,
            )

    parents = [_read_parent_id(source) for source in request.sources]
    parent_metadata_paths = _parent_metadata_paths(request.sources)
    source_manifest = _source_manifest(payloads)
    events_manifest = [event.model_dump() for event in request.events]
    lineage = {
        **request.lineage,
        "parents": parents,
        "parent_metadata_paths": parent_metadata_paths,
        "operation": "time_render",
        "source_type": "time_render",
        "operation_params": {
            "module_type": request.module_type,
            "module_id": request.module_id,
            "event_count": len(request.events),
            "source_count": len(request.sources),
            "clock": clock_summary,
            "modulators": request.modulators,
            "control_routes": request.control_routes,
            "control_snapshots": request.control_snapshots,
            "control_sources": request.control_sources,
            "pitch_engine": pitch_engine,
            "pitch_event_count": pitch_event_count,
            **(
                request.lineage.get("operation_params", {})
                if isinstance(request.lineage.get("operation_params"), dict)
                else {}
            ),
        },
    }
    source_metadata = {
        "type": "time_render",
        "module_type": request.module_type,
        "module_id": request.module_id,
        "parent_ids": parents,
    }
    prompt = request.prompt or f"{request.module_type} harvest"
    request_for_metadata = request.model_copy(
        update={
            "duration": clock_summary["loop_seconds"],
            "prompt": prompt,
            "source": source_metadata,
            "lineage": lineage,
        }
    )
    # The full events/sources lists can be huge for long sequences and we
    # already persist them in the metadata JSON; keep the in-memory job record
    # slim so /jobs/{id} and the WS event stream stay cheap.
    job_record = request_for_metadata.model_dump(exclude={"job_id", "events", "sources"})
    job_record.update(
        {
            "provider": TIME_RENDER_PROVIDER,
            "model": TIME_RENDER_MODEL,
            "event_count": len(request.events),
            "source_count": len(request.sources),
        }
    )
    job_id = storage.new_job(TIME_RENDER_MODE, job_record)
    request_for_metadata = request_for_metadata.model_copy(update={"job_id": job_id})
    audio_path, metadata_path = storage.reserve_paths(
        request=request_for_metadata,
        mode=TIME_RENDER_MODE,
        job_id=job_id,
    )[0]

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_bytes = _frames_to_bytes(mix, normalize=request.normalize)
    with wave.open(str(audio_path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(request.clock.sample_rate)
        wav.writeframes(rendered_bytes)

    time_render_metadata = {
        "clock": clock_summary,
        "module_type": request.module_type,
        "module_id": request.module_id,
        "events": events_manifest,
        "sources": source_manifest,
        "normalized": request.normalize,
        "modulators": request.modulators,
        "control_routes": request.control_routes,
        "control_snapshots": request.control_snapshots,
        "control_sources": request.control_sources,
        "pitch_engine": pitch_engine,
        "pitch_event_count": pitch_event_count,
    }
    storage.write_metadata(
        metadata_path=metadata_path,
        request=request_for_metadata,
        mode=TIME_RENDER_MODE,
        provider=TIME_RENDER_PROVIDER,
        model=TIME_RENDER_MODEL,
        seed=request.seed,
        output_audio_path=audio_path,
        sample_rate=request.clock.sample_rate,
        status="done",
        extra={
            "germinator_mode": "harvest",
            "source_type": "time_render",
            "source": source_metadata,
            "time_render": time_render_metadata,
            "parents": parents,
        },
    )
    result = GenerationResult(
        job_id=job_id,
        status="done",
        audio_files=[storage.relative_path(audio_path)],
        metadata_files=[storage.relative_path(metadata_path)],
        seed=request.seed,
        duration=clock_summary["loop_seconds"],
        sample_rate=request.clock.sample_rate,
        provider=TIME_RENDER_PROVIDER,
        model=TIME_RENDER_MODEL,
        mode=TIME_RENDER_MODE,
    )
    storage.record_result(result)
    return result
