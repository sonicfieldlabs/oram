from __future__ import annotations

import array
import importlib.util
import ipaddress
import json
import math
import socket
import struct
import sys
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from oram_sa3_server.identity import LEGACY_ENGINE_NAME, PRODUCT_NAME, SOUND_MATTER_CONCEPT
from oram_sa3_server.registry import control_registry, settings, storage, strain_registry
from oram_sa3_server.schemas import (
    ControlAnalysisFeature,
    ControlAnalysisResult,
    ControlAudioAnalysisRequest,
    ControlBridgeStatus,
    ControlCVArmRequest,
    ControlCVProfile,
    ControlCVProfilesResponse,
    ControlCVRenderRequest,
    ControlCVRenderResult,
    ControlEvent,
    ControlEventsResponse,
    ControlFeatureSummary,
    ControlGeneticGraphResponse,
    ControlMIDIMessage,
    ControlMIDIResult,
    ControlOSCMessage,
    ControlOSCResult,
    ControlPortsResponse,
    ControlRoute,
    ControlRouteEnableRequest,
    ControlRoutesResponse,
)
from oram_sa3_server.storage import safe_stem, utc_now_iso


router = APIRouter(prefix="/control", tags=["control"])

MAX_CONTROL_POINTS_PER_FEATURE = 20000
MICRO_MODULE_TYPES = {
    "grain_culture",
    "particle_engine",
    "cell_splitter",
    "swarm",
    "colony",
    "membrane",
    "metabolism",
    "spectral_tissue",
    "quanta",
    "microscope",
    "incubator",
}


@router.get("/ports", response_model=ControlPortsResponse)
def list_ports() -> ControlPortsResponse:
    return ControlPortsResponse(ports=control_registry.ports())


@router.get("/routes", response_model=ControlRoutesResponse)
def list_routes() -> ControlRoutesResponse:
    return ControlRoutesResponse(routes=control_registry.list_routes())


@router.post("/routes", response_model=ControlRoute)
def save_route(route: ControlRoute) -> ControlRoute:
    try:
        return control_registry.save_route(route)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/routes/{route_id}/enable", response_model=ControlRoute)
def enable_route(route_id: str, request: ControlRouteEnableRequest) -> ControlRoute:
    try:
        return control_registry.set_route_enabled(route_id, request.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"control route not found: {route_id}") from exc


@router.delete("/routes/{route_id}")
def delete_route(route_id: str) -> dict[str, str]:
    try:
        control_registry.delete_route(route_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"control route not found: {route_id}") from exc
    return {"status": "deleted", "route_id": route_id}


@router.get("/events", response_model=ControlEventsResponse)
@router.get("/monitor", response_model=ControlEventsResponse)
def list_events() -> ControlEventsResponse:
    return ControlEventsResponse(events=control_registry.events())


@router.post("/events", response_model=ControlEvent)
def post_event(event: ControlEvent) -> ControlEvent:
    return control_registry.add_event(event)


@router.post("/panic", response_model=ControlEvent)
def panic() -> ControlEvent:
    return control_registry.panic()


@router.get("/bridge/status", response_model=ControlBridgeStatus)
def bridge_status() -> ControlBridgeStatus:
    profiles = control_registry.list_cv_profiles()
    native_midi = importlib.util.find_spec("mido") is not None
    return ControlBridgeStatus(
        osc_udp_send=True,
        osc_udp_receive=False,
        midi_browser=True,
        midi_native=native_midi,
        cv_hardware_output=False,
        cv_profiles=len(profiles),
        armed_cv_outputs=sum(1 for profile in profiles if profile.armed),
        detail={
            "osc_receive": "Use /control/osc/receive to ingest messages from an explicit local bridge.",
            "midi_native": "Optional mido backend is available." if native_midi else "Install/configure a native MIDI bridge to send outside Web MIDI.",
            "cv_hardware_output": "Physical CV output remains disabled; profiles only gate future bridge use.",
        },
    )


def _osc_padded(value: bytes) -> bytes:
    return value + (b"\0" * ((4 - (len(value) % 4)) % 4))


def _osc_packet(message: ControlOSCMessage) -> bytes:
    address = _osc_padded(message.address.encode("utf-8") + b"\0")
    tags = ","
    payload = b""
    for value in message.values:
        if isinstance(value, int) and not isinstance(value, bool):
            tags += "i"
            payload += struct.pack(">i", value)
        elif isinstance(value, float):
            tags += "f"
            payload += struct.pack(">f", value)
        else:
            tags += "s"
            payload += _osc_padded(str(value).encode("utf-8") + b"\0")
    return address + _osc_padded(tags.encode("ascii") + b"\0") + payload


def _safe_osc_target(host: str) -> str:
    try:
        resolved = socket.gethostbyname(host)
        ip = ipaddress.ip_address(resolved)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid OSC host: {host}") from exc
    if not (ip.is_loopback or ip.is_private or ip.is_link_local):
        raise HTTPException(
            status_code=422,
            detail="OSC UDP send is restricted to loopback/private/link-local targets",
        )
    return resolved


@router.post("/osc/send", response_model=ControlOSCResult)
def send_osc(message: ControlOSCMessage) -> ControlOSCResult:
    target = _safe_osc_target(message.host)
    packet = _osc_packet(message)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            byte_count = sock.sendto(packet, (target, message.port))
    except OSError as exc:
        control_registry.add_event(
            ControlEvent(
                kind="osc",
                source="osc_udp_send",
                value={"host": message.host, "port": message.port, "address": message.address},
                metadata={"sent": False, "error": str(exc), **message.metadata},
            )
        )
        return ControlOSCResult(
            status="error",
            host=message.host,
            port=message.port,
            address=message.address,
            error=str(exc),
        )
    control_registry.add_event(
        ControlEvent(
            kind="osc",
            source="osc_udp_send",
            value={"host": message.host, "port": message.port, "address": message.address, "values": message.values},
            metadata={"sent": True, "byte_count": byte_count, **message.metadata},
        )
    )
    return ControlOSCResult(
        status="sent",
        host=message.host,
        port=message.port,
        address=message.address,
        byte_count=byte_count,
        sent=True,
    )


@router.post("/osc/receive", response_model=ControlOSCResult)
def receive_osc(message: ControlOSCMessage) -> ControlOSCResult:
    control_registry.add_event(
        ControlEvent(
            kind="osc",
            source="osc_bridge_receive",
            value={"host": message.host, "port": message.port, "address": message.address, "values": message.values},
            metadata={"ingested": True, **message.metadata},
        )
    )
    return ControlOSCResult(
        status="recorded",
        host=message.host,
        port=message.port,
        address=message.address,
        byte_count=0,
        sent=False,
    )


def _midi_status_byte(message: ControlMIDIMessage) -> int:
    channel = max(0, min(15, message.channel - 1))
    return {
        "note_off": 0x80,
        "note_on": 0x90,
        "cc": 0xB0,
    }.get(message.type, 0xB0) + channel


def _midi_bytes(message: ControlMIDIMessage) -> list[int]:
    if message.type == "clock":
        return [0xF8]
    if message.type == "transport":
        return [0xFA if message.value > 0 else 0xFC]
    if message.type in {"note_on", "note_off"}:
        return [_midi_status_byte(message), int(message.note or 60), int(message.velocity)]
    return [_midi_status_byte(message), int(message.cc or 1), int(message.value)]


@router.post("/midi/send", response_model=ControlMIDIResult)
def send_midi(message: ControlMIDIMessage) -> ControlMIDIResult:
    if message.backend == "event" or message.backend == "browser":
        control_registry.add_event(
            ControlEvent(
                kind="midi",
                source=f"midi_{message.backend}",
                value={"bytes": _midi_bytes(message), "type": message.type, "device": message.device},
                metadata={**message.metadata, "sent": message.backend == "browser"},
            )
        )
        return ControlMIDIResult(
            status="recorded",
            sent=False,
            backend=message.backend,
            detail="Use browser Web MIDI for live device output; server recorded the intent.",
        )
    if importlib.util.find_spec("mido") is None:
        control_registry.add_event(
            ControlEvent(
                kind="midi",
                source="midi_native_optional",
                value={"bytes": _midi_bytes(message), "type": message.type, "device": message.device},
                metadata={**message.metadata, "sent": False, "missing": "mido"},
            )
        )
        return ControlMIDIResult(
            status="unsupported",
            sent=False,
            backend="native_optional",
            detail="Native MIDI requires an installed/configured mido backend.",
        )
    try:
        import mido  # type: ignore[import-not-found]

        midi_type = "control_change" if message.type == "cc" else message.type
        kwargs: dict[str, Any] = {"channel": message.channel - 1}
        if message.type == "cc":
            kwargs.update({"control": message.cc or 1, "value": message.value})
        elif message.type in {"note_on", "note_off"}:
            kwargs.update({"note": message.note or 60, "velocity": message.velocity})
        mido_message = mido.Message(midi_type, **kwargs)
        with mido.open_output(message.device) as output:
            output.send(mido_message)
    except Exception as exc:
        return ControlMIDIResult(status="error", sent=False, backend="native_optional", detail=str(exc))
    control_registry.add_event(
        ControlEvent(
            kind="midi",
            source="midi_native_optional",
            value={"bytes": _midi_bytes(message), "type": message.type, "device": message.device},
            metadata={**message.metadata, "sent": True},
        )
    )
    return ControlMIDIResult(status="sent", sent=True, backend="native_optional")


@router.get("/cv/profiles", response_model=ControlCVProfilesResponse)
def list_cv_profiles() -> ControlCVProfilesResponse:
    return ControlCVProfilesResponse(profiles=control_registry.list_cv_profiles())


@router.post("/cv/profiles", response_model=ControlCVProfile)
def save_cv_profile(profile: ControlCVProfile) -> ControlCVProfile:
    try:
        return control_registry.save_cv_profile(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cv/profiles/{profile_id}/arm", response_model=ControlCVProfile)
def arm_cv_profile(profile_id: str, request: ControlCVArmRequest) -> ControlCVProfile:
    try:
        return control_registry.set_cv_profile_armed(
            profile_id,
            request.armed,
            confirm=request.confirm,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"CV profile not found: {profile_id}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _metadata_items_for_control_graph(limit: int = 300) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(storage.metadata_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        if len(items) >= limit:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        data["_metadata_path"] = storage.relative_path(path)
        items.append(data)
    return items


def _micro_profile_items_for_control_graph(limit: int = 300) -> list[dict[str, Any]]:
    micro_dir = settings.output_root / "micro"
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
        if not isinstance(data, dict):
            continue
        data["_profile_file"] = storage.relative_path(path)
        items.append(data)
    return items


def _strain_node_id(strain: dict[str, Any]) -> str:
    key = strain.get("id") or strain.get("name") or strain.get("path") or "unknown"
    return f"strain:{safe_stem(str(key), fallback='unknown')}"


def _strain_records(item: dict[str, Any], lineage: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in ("strain_stack", "lora_strains", "lora"):
        value = item.get(key)
        if isinstance(value, list):
            records.extend(record for record in value if isinstance(record, dict))
    lineage_records = lineage.get("lora_strains")
    if isinstance(lineage_records, list):
        records.extend(record for record in lineage_records if isinstance(record, dict))
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        node_id = _strain_node_id(record)
        unique[node_id] = record
    return list(unique.values())


def _metadata_path_keys(*values: Any) -> set[str]:
    return {str(value) for value in values if value not in (None, "")}


@router.get("/genetic/control-graph", response_model=ControlGeneticGraphResponse)
def control_genetic_graph(limit: int = 300) -> ControlGeneticGraphResponse:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    metadata_limit = max(1, min(limit, 1000))
    metadata_items = _metadata_items_for_control_graph(metadata_limit)
    metadata_path_to_sound: dict[str, str] = {}
    audio_path_to_sound: dict[str, str] = {}

    for strain in strain_registry.list_strains():
        strain_data = strain.model_dump(mode="json")
        node_id = _strain_node_id(strain_data)
        nodes[node_id] = {
            "id": node_id,
            "type": "strain",
            "label": strain.name,
            "path": strain.path,
            "tags": strain.tags,
            "enabled": strain.enabled,
        }

    for item in metadata_items:
        lineage = item.get("lineage") if isinstance(item.get("lineage"), dict) else {}
        sound_id = str(item.get("sound_id") or lineage.get("id") or item.get("_metadata_path"))
        nodes[sound_id] = {
            "id": sound_id,
            "type": "sound",
            "label": item.get("prompt") or Path(str(item.get("output_audio_path") or sound_id)).stem,
            "metadata_path": item.get("_metadata_path"),
            "mode": item.get("mode") or item.get("germinator_mode"),
            "created_at": item.get("created_at"),
        }
        for key in _metadata_path_keys(
            item.get("_metadata_path"),
            item.get("metadata_path"),
            item.get("absolute_metadata_path"),
        ):
            metadata_path_to_sound[key] = sound_id
        for key in _metadata_path_keys(
            item.get("output_audio_path"),
            item.get("absolute_output_audio_path"),
            lineage.get("audio_path"),
        ):
            audio_path_to_sound[key] = sound_id

        parents = item.get("parents") if isinstance(item.get("parents"), list) else lineage.get("parents", [])
        for parent in parents or []:
            parent_id = str(parent)
            nodes.setdefault(parent_id, {"id": parent_id, "type": "sound", "label": parent_id})
            edges.append({"from": parent_id, "to": sound_id, "type": "parent"})

        for strain in _strain_records(item, lineage):
            node_id = _strain_node_id(strain)
            nodes.setdefault(
                node_id,
                {
                    "id": node_id,
                    "type": "strain",
                    "label": strain.get("name") or Path(str(strain.get("path") or node_id)).stem,
                    "path": strain.get("path"),
                    "tags": strain.get("tags", []),
                },
            )
            edges.append({"from": node_id, "to": sound_id, "type": "strain-applied"})

        semantic_effects = item.get("semantic_effects")
        if not isinstance(semantic_effects, list):
            semantic_effects = lineage.get("semantic_effects") if isinstance(lineage.get("semantic_effects"), list) else []
        for effect in semantic_effects:
            if not isinstance(effect, dict):
                continue
            fx_type = str(effect.get("fx_type") or effect.get("type") or "")
            if fx_type not in MICRO_MODULE_TYPES:
                continue
            module_id = str(effect.get("module_id") or effect.get("id") or fx_type)
            node_id = f"micro_module:{safe_stem(module_id, fallback=fx_type)}"
            nodes[node_id] = {
                "id": node_id,
                "type": "micro_module",
                "label": fx_type.replace("_", " "),
                "fx_type": fx_type,
                "module_id": module_id,
                "amount": effect.get("amount"),
            }
            edges.append({"from": node_id, "to": sound_id, "type": "micro-shape"})

        control_routes = item.get("control_routes")
        if not isinstance(control_routes, list):
            control_routes = lineage.get("control_routes") if isinstance(lineage.get("control_routes"), list) else []
        for route in control_routes:
            if not isinstance(route, dict):
                continue
            route_id = str(route.get("id") or f"control_route_{len(nodes)}")
            nodes[route_id] = {
                "id": route_id,
                "type": "control_route",
                "label": route.get("target_label") or route.get("target_path") or route_id,
                "source_type": route.get("source_type"),
                "target_path": route.get("target_path"),
            }
            source_id = str(route.get("source_node_id") or route.get("source_port_id") or "control_source")
            nodes.setdefault(
                source_id,
                {
                    "id": source_id,
                    "type": "control_source",
                    "label": route.get("source_label") or route.get("source_type") or source_id,
                },
            )
            edges.append(
                {
                    "from": source_id,
                    "to": route_id,
                    "type": route.get("lineage_role") or "control-parent",
                }
            )
            edges.append({"from": route_id, "to": sound_id, "type": "controlled-result"})

    for event in control_registry.events():
        event_id = event.id or f"event_{len(nodes)}"
        nodes[event_id] = {
            "id": event_id,
            "type": "control_event",
            "label": event.source,
            "kind": event.kind,
            "timestamp": event.timestamp,
        }
        route_id = event.route_id
        if route_id and route_id in nodes:
            edges.append({"from": route_id, "to": event_id, "type": "emitted-event"})

    micro_profiles = _micro_profile_items_for_control_graph(metadata_limit)
    for profile in micro_profiles:
        profile_id = str(profile.get("id") or profile.get("_profile_file"))
        node_id = f"micro_profile:{safe_stem(profile_id, fallback='profile')}"
        nodes[node_id] = {
            "id": node_id,
            "type": "micro_profile",
            "label": profile.get("module") or "micro profile",
            "profile_file": profile.get("_profile_file"),
            "input_audio_path": profile.get("input_audio_path"),
            "descriptors": profile.get("descriptors", {}),
            "created_at": profile.get("created_at"),
        }
        source_id = str(profile.get("source_id") or "")
        source_sound = None
        if source_id and source_id in nodes:
            source_sound = source_id
        for key in _metadata_path_keys(profile.get("metadata_path")):
            source_sound = source_sound or metadata_path_to_sound.get(key)
        for key in _metadata_path_keys(profile.get("input_audio_path")):
            source_sound = source_sound or audio_path_to_sound.get(key)
        if source_sound:
            edges.append({"from": source_sound, "to": node_id, "type": "micro-profiled"})

    return ControlGeneticGraphResponse(
        nodes=list(nodes.values()),
        edges=edges,
        source={
            "metadata_count": len(metadata_items),
            "node_count": len(nodes),
            "event_count": len(control_registry.events()),
            "strain_count": sum(1 for node in nodes.values() if node.get("type") == "strain"),
            "micro_profile_count": len(micro_profiles),
            "limit": limit,
        },
    )


def _resolve_output_wav(path: str) -> Path:
    try:
        target = storage.resolve_existing_path(path, label="audio")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        target.relative_to(settings.output_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Only germ output audio can be analyzed.") from exc
    if target.suffix.lower() != ".wav":
        raise HTTPException(status_code=422, detail="Control analysis currently requires WAV source files.")
    return target


def _read_pcm16_wav(path: Path) -> tuple[array.array, int, int, int]:
    try:
        with wave.open(str(path), "rb") as wav:
            if wav.getcomptype() != "NONE":
                raise HTTPException(status_code=422, detail="Compressed WAV files are not supported.")
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frame_count = wav.getnframes()
            raw = wav.readframes(frame_count)
    except wave.Error as exc:
        raise HTTPException(status_code=422, detail=f"Invalid WAV file: {exc}") from exc
    if sample_width != 2:
        raise HTTPException(status_code=422, detail="Control analysis requires 16-bit PCM WAV audio.")
    if channels not in {1, 2}:
        raise HTTPException(status_code=422, detail="Control analysis supports mono or stereo WAV audio.")
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples, channels, sample_rate, frame_count


def _frame_value(samples: array.array, channels: int, frame: int) -> float:
    index = frame * channels
    if channels == 1:
        return samples[index] / 32768.0
    return ((samples[index] + samples[index + 1]) * 0.5) / 32768.0


def _smooth_values(values: list[float], smooth: float) -> list[float]:
    if not values or smooth <= 0:
        return values
    alpha = max(0.001, min(1.0, 1.0 - smooth))
    current = values[0]
    output = []
    for value in values:
        current = current + (value - current) * alpha
        output.append(current)
    return output


def _normalize_values(values: list[float]) -> list[float]:
    peak = max((abs(value) for value in values), default=0.0)
    if peak <= 0:
        return values
    return [max(0.0, min(1.0, value / peak)) for value in values]


def _estimated_pitch_hz(samples: array.array, channels: int, start: int, end: int, sample_rate: int) -> float:
    previous = _frame_value(samples, channels, start)
    crossings = 0
    for frame in range(start + 1, end):
        value = _frame_value(samples, channels, frame)
        if (previous <= 0 < value) or (previous >= 0 > value):
            crossings += 1
        previous = value
    duration = max(1e-9, (end - start) / sample_rate)
    frequency = crossings / (2.0 * duration)
    if frequency < 20.0 or frequency > 5000.0:
        return 0.0
    return frequency


def _chroma_unit(frequency: float) -> float:
    if frequency <= 0:
        return 0.0
    midi = round(69 + 12 * math.log2(frequency / 440.0))
    return (midi % 12) / 11.0


def _decimate_points(points: list[dict[str, float]]) -> list[dict[str, float]]:
    if len(points) <= MAX_CONTROL_POINTS_PER_FEATURE:
        return points
    stride = math.ceil(len(points) / MAX_CONTROL_POINTS_PER_FEATURE)
    return points[::stride]


def _feature_summary(
    feature: ControlAnalysisFeature,
    points: list[dict[str, float]],
) -> ControlFeatureSummary:
    values = [point["value"] for point in points]
    if not values:
        return ControlFeatureSummary(feature=feature, point_count=0, min=0, max=0, mean=0)
    max_value = max(values)
    peak_index = values.index(max_value)
    event_count = 0
    if feature == "transient":
        event_count = sum(1 for value in values if value >= 0.5)
    return ControlFeatureSummary(
        feature=feature,
        point_count=len(points),
        min=min(values),
        max=max_value,
        mean=sum(values) / len(values),
        peak_time_sec=points[peak_index]["t"],
        event_count=event_count,
    )


def _analyze_features(
    *,
    samples: array.array,
    channels: int,
    sample_rate: int,
    frame_count: int,
    request: ControlAudioAnalysisRequest,
) -> tuple[dict[str, list[dict[str, float]]], list[ControlFeatureSummary]]:
    window_frames = max(1, round((request.window_ms / 1000.0) * sample_rate))
    hop_frames = max(1, round((request.hop_ms / 1000.0) * sample_rate))
    raw_values: dict[str, list[float]] = {feature: [] for feature in request.features}
    transient_values: list[float] = []
    times: list[float] = []
    previous_rms = 0.0
    frame = 0
    while frame < frame_count:
        end = min(frame_count, frame + window_frames)
        count = max(1, end - frame)
        peak = 0.0
        total = 0.0
        square_total = 0.0
        diff_total = 0.0
        previous_value = _frame_value(samples, channels, frame)
        for item_frame in range(frame, end):
            value = _frame_value(samples, channels, item_frame)
            absolute = abs(value)
            peak = max(peak, absolute)
            total += absolute
            square_total += value * value
            diff_total += abs(value - previous_value)
            previous_value = value
        rms = math.sqrt(square_total / count)
        envelope = peak
        transient = max(0.0, rms - previous_rms)
        previous_rms = rms
        spectral_proxy = min(1.0, diff_total / max(total, 1e-9))
        pitch_hz = _estimated_pitch_hz(samples, channels, frame, end, sample_rate)
        feature_values = {
            "envelope": envelope,
            "rms": rms,
            "transient": transient,
            "spectral_centroid": spectral_proxy,
            "pitch": min(1.0, pitch_hz / 2000.0),
            "chroma": _chroma_unit(pitch_hz),
            "onset_density": 0.0,
            "tempo": 0.0,
            "timbre": min(1.0, (spectral_proxy * 0.7) + (rms * 0.3)),
        }
        times.append(frame / sample_rate)
        transient_values.append(transient)
        for feature in request.features:
            raw_values[feature].append(feature_values[feature])
        frame += hop_frames

    if "onset_density" in request.features or "tempo" in request.features:
        transient_peak = max(transient_values, default=0.0)
        threshold = transient_peak * 0.45 if transient_peak > 0 else 1.0
        onset_flags = [1 if value >= threshold and value > 0 else 0 for value in transient_values]
        if "onset_density" in request.features:
            radius = 4
            density_values = []
            for index in range(len(onset_flags)):
                start = max(0, index - radius)
                end = min(len(onset_flags), index + radius + 1)
                density_values.append(sum(onset_flags[start:end]) / max(1, end - start))
            raw_values["onset_density"] = density_values
        if "tempo" in request.features:
            onset_times = [times[index] for index, flag in enumerate(onset_flags) if flag]
            intervals = [
                right - left
                for left, right in zip(onset_times, onset_times[1:])
                if 0.05 <= right - left <= 4.0
            ]
            if intervals:
                mean_interval = sum(intervals) / len(intervals)
                bpm = max(20.0, min(300.0, 60.0 / mean_interval))
                tempo_unit = bpm / 300.0
            else:
                tempo_unit = 0.0
            raw_values["tempo"] = [tempo_unit for _ in times]

    feature_points: dict[str, list[dict[str, float]]] = {}
    summaries: list[ControlFeatureSummary] = []
    for feature in request.features:
        values = _smooth_values(raw_values[feature], request.smooth)
        if request.normalize:
            values = _normalize_values(values)
        points = [
            {"t": round(times[index], 6), "value": round(max(0.0, min(1.0, value)), 6)}
            for index, value in enumerate(values)
        ]
        points = _decimate_points(points)
        feature_points[feature] = points
        summaries.append(_feature_summary(feature, points))
    return feature_points, summaries


def _route_suggestions(features: list[ControlFeatureSummary]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for summary in features:
        if summary.feature == "transient":
            suggestions.append(
                {
                    "source_port_id": "mod:audio_to_control",
                    "target_port_id": "time:event_probability",
                    "label": "Transients -> event probability",
                    "feature": summary.feature,
                    "target_kind": "control",
                }
            )
        elif summary.feature == "spectral_centroid":
            suggestions.append(
                {
                    "source_port_id": "mod:audio_to_control",
                    "target_port_id": "generation:brightness_language",
                    "label": "Brightness -> generation language",
                    "feature": summary.feature,
                    "target_kind": "control",
                }
            )
        elif summary.feature in {"pitch", "chroma", "tempo", "onset_density"}:
            suggestions.append(
                {
                    "source_port_id": "mod:audio_to_control",
                    "target_port_id": "time:event_velocity",
                    "label": f"{summary.feature.replace('_', ' ').title()} -> time velocity",
                    "feature": summary.feature,
                    "target_kind": "control",
                }
            )
        elif summary.feature == "timbre":
            suggestions.append(
                {
                    "source_port_id": "mod:audio_to_control",
                    "target_port_id": "generation:batch_spread",
                    "label": "Timbre -> batch spread",
                    "feature": summary.feature,
                    "target_kind": "control",
                }
            )
        else:
            suggestions.append(
                {
                    "source_port_id": "mod:audio_to_control",
                    "target_port_id": "generation:seed_drift",
                    "label": f"{summary.feature.replace('_', ' ').title()} -> seed drift",
                    "feature": summary.feature,
                    "target_kind": "control",
                }
            )
    return suggestions


@router.post("/analyze-audio", response_model=ControlAnalysisResult)
def analyze_audio(request: ControlAudioAnalysisRequest) -> ControlAnalysisResult:
    source_path = _resolve_output_wav(request.input_audio_path)
    samples, channels, sample_rate, frame_count = _read_pcm16_wav(source_path)
    duration = frame_count / float(sample_rate)
    features, summaries = _analyze_features(
        samples=samples,
        channels=channels,
        sample_rate=sample_rate,
        frame_count=frame_count,
        request=request,
    )
    analysis_id = f"control_{uuid4().hex[:12]}"
    base = safe_stem(request.output_name, fallback=f"{source_path.stem}_control")
    control_path = control_registry.control_dir / f"{base}_{analysis_id}.json"
    suggestions = _route_suggestions(summaries)
    parent_paths = [request.metadata_path] if request.metadata_path else []
    artifact = {
        "app": PRODUCT_NAME,
        "product": PRODUCT_NAME,
        "legacy_app": LEGACY_ENGINE_NAME,
        "concept": SOUND_MATTER_CONCEPT,
        "type": "control_analysis",
        "id": analysis_id,
        "created_at": utc_now_iso(),
        "input_audio_path": storage.relative_path(source_path),
        "metadata_path": request.metadata_path,
        "source_id": request.source_id,
        "sample_rate": sample_rate,
        "duration": duration,
        "window_ms": request.window_ms,
        "hop_ms": request.hop_ms,
        "smooth": request.smooth,
        "normalize": request.normalize,
        "features": features,
        "summaries": [summary.model_dump(mode="json") for summary in summaries],
        "route_suggestions": suggestions,
        "lineage": {
            **request.lineage,
            "id": analysis_id,
            "parents": request.lineage.get("parents", []),
            "parent_metadata_paths": parent_paths,
            "operation": "control_analysis",
            "source_type": "control",
            "operation_params": {
                "features": request.features,
                "input_audio_path": storage.relative_path(source_path),
                "window_ms": request.window_ms,
                "hop_ms": request.hop_ms,
                "control_sources": [
                    {
                        "role": "audio-parent",
                        "path": storage.relative_path(source_path),
                        "metadata_path": request.metadata_path,
                    }
                ],
            },
        },
    }
    storage.write_json_atomic(control_path, artifact)
    control_registry.add_event(
        ControlEvent(
            kind="control",
            source="audio_analysis",
            value={"analysis_id": analysis_id, "features": request.features},
            metadata={"control_file": storage.relative_path(control_path)},
        )
    )
    return ControlAnalysisResult(
        id=analysis_id,
        status="done",
        input_audio_path=storage.relative_path(source_path),
        control_files=[storage.relative_path(control_path)],
        metadata_file=storage.relative_path(control_path),
        sample_rate=sample_rate,
        duration=duration,
        features=summaries,
        route_suggestions=suggestions,
    )


def _load_control_points(request: ControlCVRenderRequest) -> list[dict[str, float]]:
    if request.points:
        return [point.model_dump() for point in sorted(request.points, key=lambda item: item.t)]
    if not request.input_control_path:
        return []
    try:
        artifact_path = control_registry.resolve_control_artifact(request.input_control_path)
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid control artifact: {exc}") from exc
    features = artifact.get("features") if isinstance(artifact.get("features"), dict) else {}
    if not features:
        raise HTTPException(status_code=422, detail="control artifact has no features")
    feature = request.feature or next(iter(features))
    points = features.get(feature)
    if not isinstance(points, list) or not points:
        raise HTTPException(status_code=422, detail=f"control feature not found: {feature}")
    return [
        {
            "t": max(0.0, float(point.get("t", 0.0))),
            "value": max(-1.0, min(1.0, float(point.get("value", 0.0)))),
        }
        for point in points
        if isinstance(point, dict)
    ]


def _interpolate(points: list[dict[str, float]], t: float) -> float:
    if not points:
        return 0.0
    if t <= points[0]["t"]:
        return points[0]["value"]
    for index in range(1, len(points)):
        left = points[index - 1]
        right = points[index]
        if t <= right["t"]:
            span = max(1e-9, right["t"] - left["t"])
            unit = (t - left["t"]) / span
            return left["value"] + (right["value"] - left["value"]) * unit
    return points[-1]["value"]


def _cv_signal_value(request: ControlCVRenderRequest, value: float) -> float:
    if request.mode in {"gate", "clock"}:
        value = request.gate_value if value >= 0.5 else 0.0
    elif request.range == "unipolar":
        value = max(0.0, min(1.0, value))
    else:
        value = max(-1.0, min(1.0, (value * 2.0) - 1.0))
    value = (value * request.scale) + request.offset
    return max(request.clamp_min, min(request.clamp_max, value))


def _render_cv_bytes(request: ControlCVRenderRequest, points: list[dict[str, float]]) -> bytes:
    frame_count = max(1, round(request.duration * request.sample_rate))
    out = array.array("h")
    last_value = 0.0
    max_delta = None
    if request.slew_ms > 0:
        max_delta = 1.0 / max(1.0, (request.slew_ms / 1000.0) * request.sample_rate)
    for frame in range(frame_count):
        t = frame / request.sample_rate
        value = _cv_signal_value(request, _interpolate(points, t))
        if max_delta is not None:
            delta = max(-max_delta, min(max_delta, value - last_value))
            value = last_value + delta
        last_value = value
        out.append(max(-32768, min(32767, int(round(value * 32767.0)))))
    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes()


@router.post("/render-cv", response_model=ControlCVRenderResult)
def render_cv(request: ControlCVRenderRequest) -> ControlCVRenderResult:
    points = _load_control_points(request)
    if not points:
        raise HTTPException(status_code=422, detail="no control points available for CV render")
    render_id = f"cv_{uuid4().hex[:12]}"
    base = safe_stem(request.output_name, fallback="cv_control_render")
    audio_path = control_registry.control_dir / f"{base}_{render_id}.wav"
    metadata_path = control_registry.control_dir / f"{base}_{render_id}.json"
    rendered = _render_cv_bytes(request, points)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(audio_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(request.sample_rate)
        wav.writeframes(rendered)
    metadata = {
        "app": PRODUCT_NAME,
        "product": PRODUCT_NAME,
        "legacy_app": LEGACY_ENGINE_NAME,
        "concept": SOUND_MATTER_CONCEPT,
        "type": "cv_safe_render",
        "id": render_id,
        "created_at": utc_now_iso(),
        "audio_path": storage.relative_path(audio_path),
        "duration": request.duration,
        "sample_rate": request.sample_rate,
        "mode": request.mode,
        "range": request.range,
        "scale": request.scale,
        "offset": request.offset,
        "clamp_min": request.clamp_min,
        "clamp_max": request.clamp_max,
        "slew_ms": request.slew_ms,
        "cv_safe": True,
        "hardware_output": False,
        "speaker_protection": "artifact_only_not_routed_to_audio_outputs",
        "source_control_path": request.input_control_path,
        "feature": request.feature,
        "lineage": {
            **request.lineage,
            "id": render_id,
            "operation": "cv_safe_render",
            "source_type": "control",
            "operation_params": {
                "mode": request.mode,
                "range": request.range,
                "input_control_path": request.input_control_path,
                "feature": request.feature,
                "hardware_output": False,
            },
        },
    }
    storage.write_json_atomic(metadata_path, metadata)
    control_registry.add_event(
        ControlEvent(
            kind="cv",
            source="cv_safe_render",
            value={"render_id": render_id, "mode": request.mode, "hardware_output": False},
            metadata={"audio_file": storage.relative_path(audio_path)},
        )
    )
    return ControlCVRenderResult(
        status="done",
        audio_file=storage.relative_path(audio_path),
        metadata_file=storage.relative_path(metadata_path),
        duration=request.duration,
        sample_rate=request.sample_rate,
        mode=request.mode,
        cv_safe=True,
        hardware_output=False,
    )
