"""oram.web.server — FastAPI + WebSocket server for the ORAM v2 dashboard.

exposes:
  GET  /               → serves the dashboard HTML
  GET  /api/state      → current session state JSON
  POST /api/command    → send a text command
  POST /api/listen     → listen to a layer
  POST /api/generate   → generate from a layer
  POST /api/fork       → fork a layer
  WS   /ws             → real-time bidirectional state + commands

SECURITY: API keys are loaded from .env via config.py and never
exposed to the frontend. The /api/state endpoint strips all secrets.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from oram import __version__
from oram.agent.controller import AgentController
from oram.agent.llm_adapter import LLMCliAdapter
from oram.audio.engine import MockAudioEngine, UnavailableAudioEngine
from oram.audio.importer import MAX_UPLOAD_BYTES, assign_imported_audio, decode_audio_bytes
from oram.audio.layer import LayerManager
from oram.command.router import ActionRouter
from oram.command.schemas import (
    ForkLayerAction,
    GenerateFromAction,
    ListenAction,
    SetLayerModeAction,
    SetLoopRegionAction,
)
from oram.config import OramConfig, load_dotenv
from oram.engines.registry import EngineRegistry
from oram.engines.router import EngineRouter
from oram.gateway.usage import UsageTracker
from oram.summon.mock import MockSoundGenerator
from oram.types import (
    GenerationEngine,
    Layer,
    LayerMode,
    LayerState,
    ListeningRoute,
    Mode,
    OramSession,
    SourceType,
)
from oram_security import redact_text

# ── state ──

_session: OramSession | None = None
_layer_manager: LayerManager | None = None
_engine: MockAudioEngine | None = None
_router: ActionRouter | None = None
_agent: AgentController | None = None
_connections: list[WebSocket] = []
_log_messages: list[str] = []
_config: OramConfig | None = None
_engine_registry: EngineRegistry | None = None
_engine_router: EngineRouter | None = None
_UNDO_LIMIT = 16
_undo_stack: list[dict[str, Any]] = []
_redo_stack: list[dict[str, Any]] = []
_history_lock = threading.Lock()


def _snapshot_layer(layer: Layer) -> dict[str, Any]:
    """Capture a layer for undo/redo, including its audio buffer."""
    snapshot_state = layer.state
    if snapshot_state == LayerState.RECORDING:
        snapshot_state = LayerState.EMPTY if layer.is_empty else LayerState.ACTIVE
    return {
        "id": layer.id,
        "name": layer.name,
        "slot": layer.slot,
        "source_type": layer.source_type.value,
        "buffer": layer.buffer.copy(),
        "waveform_data": list(layer.waveform_data),
        "waveform_revision": layer.waveform_revision,
        "sample_rate": layer.sample_rate,
        "channels": layer.channels,
        "duration_seconds": layer.duration_seconds,
        "playhead": layer.playhead,
        "volume": layer.volume,
        "pan": layer.pan,
        "muted": layer.muted,
        "solo": layer.solo,
        "state": snapshot_state.value,
        "layer_mode": layer.layer_mode.value,
        "looper": vars(layer.looper).copy(),
        "sampler": {
            "root_note": layer.sampler.root_note,
            "mode": layer.sampler.mode,
            "adsr": vars(layer.sampler.adsr).copy(),
            "start_point": layer.sampler.start_point,
            "end_point": layer.sampler.end_point,
            "reverse": layer.sampler.reverse,
            "transpose": layer.sampler.transpose,
            "fine_tune": layer.sampler.fine_tune,
            "polyphony": layer.sampler.polyphony,
            "velocity_sensitivity": layer.sampler.velocity_sensitivity,
        },
        "reverse": layer.reverse,
        "inpaint_regions": list(layer.inpaint_regions),
        "speed": layer.speed,
        "pitch_semitones": layer.pitch_semitones,
        "filter_type": layer.filter_type,
        "filter_cutoff_hz": layer.filter_cutoff_hz,
        "reverb_amount": layer.reverb_amount,
        "grain_density": layer.grain_density,
        "grain_size_ms": layer.grain_size_ms,
        "grain_jitter": layer.grain_jitter,
        "effects_applied": list(layer.effects_applied),
        "agent_listening": layer.agent_listening,
        "listening_route": layer.listening_route.value,
        "generation_engine": layer.generation_engine.value,
        "engine_provider": layer.engine_provider,
        "generation_prompt": layer.generation_prompt,
        "parent_layer_id": layer.parent_layer_id,
        "generation_depth": layer.generation_depth,
        "is_generated": layer.is_generated,
    }


def _restore_layer(layer: Layer, snapshot: dict[str, Any]) -> None:
    """Restore one layer from a history snapshot."""
    with layer._buf_lock:
        layer.id = snapshot["id"]
        layer.name = snapshot["name"]
        layer.slot = int(snapshot["slot"])
        layer.source_type = SourceType(snapshot["source_type"])
        layer.buffer = np.asarray(snapshot["buffer"], dtype=np.float32).copy()
        layer.waveform_data = list(snapshot.get("waveform_data", []))
        previous_revision = layer.waveform_revision
        layer.sample_rate = int(snapshot["sample_rate"])
        layer.channels = int(snapshot["channels"])
        layer.duration_seconds = float(snapshot["duration_seconds"])
        layer.playhead = min(int(snapshot["playhead"]), max(0, layer.length_samples - 1))
        layer.volume = float(snapshot["volume"])
        layer.pan = float(snapshot["pan"])
        layer.muted = bool(snapshot["muted"])
        layer.solo = bool(snapshot["solo"])
        layer.state = LayerState(snapshot["state"])
        layer.layer_mode = LayerMode(snapshot["layer_mode"])

        for key, value in snapshot["looper"].items():
            setattr(layer.looper, key, value)
        sampler = snapshot["sampler"]
        for key, value in sampler.items():
            if key == "adsr":
                for adsr_key, adsr_value in value.items():
                    setattr(layer.sampler.adsr, adsr_key, adsr_value)
            else:
                setattr(layer.sampler, key, value)

        layer.reverse = bool(snapshot["reverse"])
        layer.inpaint_regions = [tuple(region) for region in snapshot.get("inpaint_regions", [])]
        layer.speed = float(snapshot["speed"])
        layer.pitch_semitones = float(snapshot["pitch_semitones"])
        layer.filter_type = snapshot["filter_type"]
        layer.filter_cutoff_hz = snapshot["filter_cutoff_hz"]
        layer.reverb_amount = float(snapshot["reverb_amount"])
        layer.grain_density = float(snapshot["grain_density"])
        layer.grain_size_ms = float(snapshot["grain_size_ms"])
        layer.grain_jitter = float(snapshot["grain_jitter"])
        layer.effects_applied = list(snapshot["effects_applied"])
        layer.agent_listening = bool(snapshot["agent_listening"])
        layer.listening_route = ListeningRoute(snapshot["listening_route"])
        layer.generation_engine = GenerationEngine(snapshot["generation_engine"])
        layer.engine_provider = snapshot["engine_provider"]
        layer.generation_prompt = snapshot["generation_prompt"]
        layer.parent_layer_id = snapshot["parent_layer_id"]
        layer.generation_depth = int(snapshot["generation_depth"])
        layer.is_generated = bool(snapshot["is_generated"])
        layer.waveform_revision = max(previous_revision + 1, int(snapshot["waveform_revision"]) + 1)

    if not layer.is_empty:
        layer.compute_waveform()
    else:
        layer.waveform_data = [0.0] * 64


def _session_snapshot(label: str) -> dict[str, Any] | None:
    if _session is None or _layer_manager is None:
        return None
    return {
        "label": label,
        "timestamp": time.time(),
        "selected": _layer_manager.selected,
        "session": {
            "mode": _session.mode.value,
            "selected_layer": _session.selected_layer,
            "listening": _session.listening,
            "auto_listen": _session.auto_listen,
            "input_mode": _session.input_mode,
            "bpm": _session.bpm,
            "generated_bed_id": _session.generated_bed_id,
        },
        "layers": [_snapshot_layer(layer) for layer in _layer_manager.layers],
    }


def _push_undo(label: str) -> None:
    snapshot = _session_snapshot(label)
    if snapshot is None:
        return
    with _history_lock:
        _undo_stack.append(snapshot)
        del _undo_stack[:-_UNDO_LIMIT]
        _redo_stack.clear()


def _restore_session_snapshot(snapshot: dict[str, Any]) -> None:
    if _session is None or _layer_manager is None:
        return
    if _router is not None:
        try:
            _router.kill_all_audio()
        except Exception:
            pass

    for layer, layer_snapshot in zip(_layer_manager.layers, snapshot["layers"]):
        _restore_layer(layer, layer_snapshot)

    session_data = snapshot["session"]
    selected = max(0, min(len(_layer_manager.layers) - 1, int(snapshot["selected"])))
    _layer_manager.selected = selected
    _session.layers = _layer_manager.layers
    _session.selected_layer = int(session_data.get("selected_layer", selected))
    _session.mode = Mode(session_data.get("mode", Mode.RECORD.value))
    _session.listening = bool(session_data.get("listening", False))
    _session.auto_listen = bool(session_data.get("auto_listen", False))
    _session.input_mode = session_data.get("input_mode", "prompt")
    _session.bpm = session_data.get("bpm")
    _session.generated_bed_id = session_data.get("generated_bed_id")
    _waveform_cache.clear()


def _reset_to_initial_audio_state() -> list[str]:
    """Stop every audio path and return layers/session to startup audio state."""
    results: list[str] = []
    if _router is not None:
        results.extend(_router.kill_all_audio())
        _router._pending_clear_target = None
        _router._pending_clear_ts = 0.0
    elif _engine is not None and hasattr(_engine, "stop_all_audio"):
        _engine.stop_all_audio()
        results.append("stopped audio engine state")

    if _layer_manager is not None:
        sample_rate = _layer_manager.sample_rate
        channels = _layer_manager.channels
        for slot, layer in enumerate(_layer_manager.layers):
            fresh = Layer(
                id=f"layer-{slot + 1:03d}",
                name=f"layer_{slot + 1}",
                slot=slot,
                sample_rate=sample_rate,
                channels=channels,
            )
            _restore_layer(layer, _snapshot_layer(fresh))
        _layer_manager.selected = 0
        results.append("cleared all layers")

    if _session is not None:
        _session.layers = _layer_manager.layers if _layer_manager is not None else _session.layers
        _session.mode = Mode.RECORD
        _session.selected_layer = 0
        _session.listening = False
        _session.auto_listen = bool(_config.auto_listen) if _config is not None else False
        _session.input_mode = "prompt"
        _session.bpm = None
        _session.generated_bed_id = None

    _waveform_cache.clear()
    return results


def _append_log(message: str) -> None:
    """append a bounded server log message for the dashboard."""
    _log_messages.append(redact_text(message))
    if len(_log_messages) > 50:
        _log_messages.pop(0)


def _get_state_snapshot() -> dict[str, Any]:
    """serialize the current session state for the frontend.

    IMPORTANT: never include API keys or secrets in the snapshot.
    """
    if _session is None or _layer_manager is None or _engine is None:
        return {"error": "not initialized"}

    layers = []
    for layer in _layer_manager.layers:
        # Lightweight preview only. HD waveform data is served by /api/waveform.
        if layer.is_empty:
            waveform = [0.0] * 64
        else:
            waveform = layer.waveform_data or layer.compute_waveform(64)

        playhead_pct = 0.0
        if not layer.is_empty and layer.length_samples > 0:
            playhead_pct = round((layer.playhead / layer.length_samples) * 100, 1)

        loop_end = layer.looper.end_offset if layer.looper.end_offset > 0 else layer.length_samples
        if layer.is_empty or layer.length_samples <= 0:
            loop_start_pct = 0
            loop_end_pct = 100
            loop_start_seconds = 0.0
            loop_end_seconds = 0.0
        else:
            loop_start_pct = round(layer.looper.start_offset / layer.length_samples * 100, 2)
            loop_end_pct = round(loop_end / layer.length_samples * 100, 2)
            loop_start_seconds = (
                round(layer.looper.start_offset / layer.sample_rate, 3)
                if layer.sample_rate > 0
                else 0.0
            )
            loop_end_seconds = round(loop_end / layer.sample_rate, 3) if layer.sample_rate > 0 else 0.0
        loop_len_samples = max(1, loop_end - layer.looper.start_offset)
        loop_fade_in_seconds = (
            round(layer.looper.fade_in_samples / layer.sample_rate, 3)
            if layer.sample_rate > 0
            else 0.0
        )
        loop_fade_out_seconds = (
            round(layer.looper.fade_out_samples / layer.sample_rate, 3)
            if layer.sample_rate > 0
            else 0.0
        )
        loop_fade_in_pct = (
            round(layer.looper.fade_in_samples / layer.length_samples * 100, 2)
            if layer.length_samples > 0
            else 0
        )
        loop_fade_out_pct = (
            round(layer.looper.fade_out_samples / layer.length_samples * 100, 2)
            if layer.length_samples > 0
            else 0
        )
        loop_fade_in_loop_pct = round(layer.looper.fade_in_samples / loop_len_samples * 100, 2)
        loop_fade_out_loop_pct = round(layer.looper.fade_out_samples / loop_len_samples * 100, 2)
        inpaint_regions = []
        if not layer.is_empty and layer.length_samples > 0:
            for start, end in layer.inpaint_regions:
                inpaint_regions.append({
                    "start_pct": round(start / layer.length_samples * 100, 2),
                    "end_pct": round(end / layer.length_samples * 100, 2),
                    "start_seconds": round(start / layer.sample_rate, 3) if layer.sample_rate > 0 else 0.0,
                    "end_seconds": round(end / layer.sample_rate, 3) if layer.sample_rate > 0 else 0.0,
                })

        layers.append({
            "id": layer.id,
            "slot": layer.slot + 1,
            "name": layer.name,
            "state": layer.state.value,
            "source_type": layer.source_type.value,
            "layer_mode": layer.layer_mode.value,
            "duration": round(layer.duration_seconds, 2),
            "volume": round(layer.volume, 3),
            "pan": round(layer.pan, 2),
            "muted": layer.muted,
            "solo": layer.solo,
            "reverse": layer.reverse,
            "playback_reverse": bool(layer.reverse or layer.looper.reverse or layer.sampler.reverse),
            "speed": round(layer.speed, 2),
            "pitch_semitones": round(layer.pitch_semitones, 1),
            "effects": layer.effects_applied,
            "is_generated": layer.is_generated,
            "parent_layer_id": layer.parent_layer_id,
            "generation_depth": layer.generation_depth,
            "listening_route": layer.listening_route.value,
            "generation_engine": layer.generation_engine.value,
            "waveform": waveform,
            "playhead_pct": playhead_pct,
            "waveform_revision": layer.waveform_revision,
            "loop_enabled": layer.looper.enabled,
            "loop_start_pct": loop_start_pct,
            "loop_end_pct": loop_end_pct,
            "loop_start_seconds": loop_start_seconds,
            "loop_end_seconds": loop_end_seconds,
            "loop_fade_in_pct": loop_fade_in_pct,
            "loop_fade_out_pct": loop_fade_out_pct,
            "loop_fade_in_loop_pct": loop_fade_in_loop_pct,
            "loop_fade_out_loop_pct": loop_fade_out_loop_pct,
            "loop_fade_in_seconds": loop_fade_in_seconds,
            "loop_fade_out_seconds": loop_fade_out_seconds,
            "inpaint_regions": inpaint_regions,
        })

    # gateway status (no secrets)
    gateway_status = "mock"
    if _config and _config.elevenlabs_api_key and _config.generator_backend in ("elevenlabs", "auto"):
        gateway_status = "elevenlabs"

    # engine registry info
    engines_info = []
    if _engine_registry:
        for spec in _engine_registry.list_available_engines():
            engines_info.append({
                "id": spec.id,
                "provider": spec.provider.value,
                "label": spec.label,
                "mode": spec.mode.value,
                "capabilities": [c.value for c in spec.capabilities],
                "max_duration": spec.max_duration_seconds,
                "supports_streaming": spec.supports_streaming,
                "supports_audio_input": spec.supports_audio_input,
            })

    return {
        "version": __version__,
        "mode": _session.mode.value,
        "input_mode": _session.input_mode,
        "scene": _session.scene,
        "sample_rate": _config.sample_rate if _config else _session.sample_rate,
        "bpm": _session.bpm,
        "selected_layer": _layer_manager.selected,
        "input_level": round(_engine.get_input_level(), 3),
        "output_level": round(_engine.get_output_level(), 3),
        "recording": bool(getattr(_engine, "_recording", False)),
        "master_recording": bool(
            getattr(_engine, "is_master_recording", lambda: False)()
        ),
        "master_recording_seconds": round(
            float(getattr(_engine, "get_master_recording_seconds", lambda: 0.0)()),
            2,
        ),
        "input_available": bool(getattr(_engine, "has_input", lambda: True)()),
        "audio_running": bool(_engine.is_running()),
        "auto_listen": _session.auto_listen,
        "gateway": gateway_status,
        "engines": engines_info,
        "engine_count": _engine_registry.available_count if _engine_registry else 0,
        "can_undo": bool(_undo_stack),
        "can_redo": bool(_redo_stack),
        "undo_label": _undo_stack[-1]["label"] if _undo_stack else "",
        "redo_label": _redo_stack[-1]["label"] if _redo_stack else "",
        "layers": layers,
        "log": list(_log_messages[-16:]),
        "stable_audio_service_url": _config.stable_audio_service_url if _config else "",
        "timestamp": time.time(),
    }


async def _broadcast_state():
    """broadcast state to all connected websocket clients."""
    if not _connections:
        return
    state = _get_state_snapshot()
    data = json.dumps(state)
    dead = []
    for ws in _connections:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections.remove(ws)


async def _state_broadcast_loop():
    """continuously broadcast state at ~12 fps."""
    while True:
        await _broadcast_state()
        await asyncio.sleep(1 / 12)


# ── lifecycle ──

def _build_gateway(config: OramConfig) -> dict | None:
    """build ElevenLabs gateway from config. keys stay server-side."""
    if config.generator_backend not in ("elevenlabs", "auto"):
        return None
    if not config.elevenlabs_api_key:
        return None

    gateway = {}
    try:
        from oram.gateway.sfx import SFXAdapter
        gateway["sfx"] = SFXAdapter(api_key=config.elevenlabs_api_key)
    except Exception:
        pass
    try:
        from oram.gateway.voice import VoiceAdapter
        gateway["voice"] = VoiceAdapter(api_key=config.elevenlabs_api_key)
    except Exception:
        pass
    try:
        from oram.gateway.music import MusicAdapter
        gateway["music"] = MusicAdapter(api_key=config.elevenlabs_api_key)
    except Exception:
        pass

    return gateway if gateway else None


def _on_record_complete(layer):
    """auto-listen → generate after a recording finishes."""
    if _router is None or _session is None or _layer_manager is None:
        return
    if not _session.auto_listen:
        _append_log(f"recorded layer {layer.slot + 1} ({layer.duration_seconds:.1f}s)")
        return

    audio_epoch = _router.audio_kill_epoch
    _append_log(f"recorded layer {layer.slot + 1} — listening...")

    try:
        from oram.ears.prompt_compiler import compile_prompt
        from oram.ears.routes import create_route
        from oram.gateway.router import select_engine

        route = create_route("hybrid", llm_adapter=_router.llm_adapter)
        report = route.listen(layer.buffer, layer.sample_rate)
        _router._listening_reports[layer.id] = report

        if not _router.is_audio_epoch_current(audio_epoch):
            _append_log("generation discarded after kill")
            return

        tech = report.technical
        parts = []
        if tech.dominant_pitch_note:
            parts.append(f"pitch: {tech.dominant_pitch_note}")
        if tech.key_estimate:
            parts.append(f"key: {tech.key_estimate}")
        if tech.estimated_bpm > 0:
            parts.append(f"bpm: {tech.estimated_bpm:.0f}")
        if tech.texture:
            parts.append(tech.texture)
        if tech.noise_balance:
            parts.append(tech.noise_balance)
        if report.descriptive.resembles:
            parts.append(report.descriptive.resembles)
        if report.speculative.imaginary_thing:
            parts.append(report.speculative.imaginary_thing[:50])
        _append_log(f"oram hears: {', '.join(parts)}")

        analysis_data = {
            "contains_speech": tech.contains_speech,
            "contains_voice": tech.contains_voice,
            "pitch_confidence": tech.pitch_confidence,
            "rhythmic_regularity": tech.rhythmic_regularity,
            "is_noisy": tech.is_noisy,
            "is_gestural": tech.is_gestural,
            "duration": tech.duration,
        }
        decision = select_engine(analysis_data, "auto")
        _append_log(f"engine: {decision.engine} — {decision.reason}")

        # build mix context from all active layers
        mix_ctx = None
        try:
            from oram.ears.mix_context import build_mix_context
            active_layers = [
                l for l in _layer_manager.layers
                if not l.is_empty and not l.muted
            ]
            if active_layers:
                mix_ctx = build_mix_context(active_layers, layer.sample_rate)
                if mix_ctx.dominant_pitches:
                    _append_log(f"mix context: {', '.join(mix_ctx.dominant_pitches)} — {mix_ctx.density_level}")
        except Exception:
            pass  # mix context is optional — proceed without it

        prompt = compile_prompt(report, decision.engine, mix_context=mix_ctx)
        _append_log(f"prompt: {prompt[:100]}...")

        if not _router.is_audio_epoch_current(audio_epoch):
            _append_log("generation discarded after kill")
            return

        gen_duration = min(layer.duration_seconds * 1.2, 30.0)
        audio = _router._call_engine(decision.engine, prompt, gen_duration, layer)

        if audio is None:
            _append_log("generation: no audio returned (check API key)")
            return

        if not _router.is_audio_epoch_current(audio_epoch):
            _append_log("generation discarded after kill")
            return

        _push_undo("auto generation")
        new_layer = _layer_manager.create_derived_layer(
            parent=layer,
            audio=audio,
            route="hybrid",
            engine=decision.engine,
            prompt=prompt,
        )

        if new_layer is None:
            _append_log("all layer slots full — clear a layer first")
            return

        _append_log(
            f"generated → layer {new_layer.slot + 1} "
            f"(from layer {layer.slot + 1}, depth {new_layer.generation_depth})"
        )
    except Exception as e:
        _append_log(f"auto-generate error: {e}")


def _build_audio_engine(config: OramConfig):
    """build the active audio engine for the dashboard.

    Attempts real hardware first unless mock mode is explicitly requested.
    If real audio cannot be created, keep the dashboard mounted with a silent
    unavailable engine rather than generating procedural mock audio.
    """
    use_mock = getattr(app.state, "mock_audio", False) or config.mock_audio
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        use_mock = False
    if use_mock:
        _append_log("audio: mock (configured)")
        return MockAudioEngine(
            session=_session,
            layer_manager=_layer_manager,
            sample_rate=config.sample_rate,
            block_size=config.block_size,
            on_record_complete=_on_record_complete,
        )

    try:
        from oram.audio.realtime import RealAudioEngine

        return RealAudioEngine(
            session=_session,
            layer_manager=_layer_manager,
            sample_rate=config.sample_rate,
            block_size=config.block_size,
            input_device=config.input_device,
            output_device=config.output_device,
            on_record_complete=_on_record_complete,
        )
    except Exception as e:
        _append_log(f"audio: real engine unavailable ({e}); mock disabled")
        return UnavailableAudioEngine(
            reason=str(e),
            sample_rate=config.sample_rate,
            block_size=config.block_size,
            input_device=config.input_device,
            output_device=config.output_device,
        )


def _start_audio_engine_or_fallback() -> None:
    """start the current engine and fall back to mock if hardware startup fails."""
    global _engine
    if _engine is None or _config is None:
        return

    _engine.start()
    if isinstance(_engine, MockAudioEngine):
        return
    if _engine.is_running():
        if bool(getattr(_engine, "has_input", lambda: False)()):
            _append_log("audio: real (input + speakers)")
        else:
            _append_log("audio: output only (no input device)")
        return

    reason = getattr(_engine, "reason", "hardware stream did not start")
    _append_log(f"audio: unavailable ({reason}); mock disabled")


def _restart_audio_engine() -> str:
    """restart the dashboard audio engine after device settings change."""
    global _engine
    if _config is None or _router is None:
        return "not initialized"

    if _engine is not None:
        _engine.stop()

    _engine = _build_audio_engine(_config)
    _router.engine = _engine
    _start_audio_engine_or_fallback()
    if not _engine.is_running() and not isinstance(_engine, MockAudioEngine):
        return "audio engine unavailable"
    if bool(getattr(_engine, "has_input", lambda: True)()):
        return "audio engine restarted"
    return "audio engine restarted without input"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """initialize ORAM v2 engine on startup, clean up on shutdown."""
    global _session, _layer_manager, _engine, _router, _agent, _config, _DASHBOARD_TOKEN
    global _engine_registry, _engine_router

    from oram.engines.sa3_launcher import start_sa3_server, stop_sa3_server
    sa3_url = start_sa3_server()

    load_dotenv()
    _config = OramConfig.from_env()
    if sa3_url and not os.environ.get("PYTEST_CURRENT_TEST"):
        _config.stable_audio_service_url = sa3_url
    _DASHBOARD_TOKEN = _config.dashboard_token

    session_name = f"oram_{datetime.now().strftime('%H%M%S')}"
    _session = OramSession(
        id=session_name,
        scene=session_name,
        sample_rate=_config.sample_rate,
        auto_listen=_config.auto_listen,
    )
    _layer_manager = LayerManager(sample_rate=_config.sample_rate, channels=2)
    _session.layers = _layer_manager.layers

    generator = MockSoundGenerator()

    # v2: ElevenLabs gateway (legacy fallback)
    gateway = _build_gateway(_config)
    if gateway:
        engines_list = ", ".join(gateway.keys())
        _append_log(f"gateway: elevenlabs ({engines_list})")
    else:
        _append_log("gateway: mock")

    # v3: engine registry + router
    _engine_registry = EngineRegistry.from_config(_config)
    _engine_router = None
    if _engine_registry.available_count > 0:
        _engine_router = EngineRouter(
            registry=_engine_registry,
            default_provider=_config.preferred_provider,
        )
        _append_log(f"engines: {_engine_registry.summary()}")
    else:
        _append_log("engines: none registered (mock only)")

    # v2: usage tracker
    usage_tracker = UsageTracker()

    # LLM adapter
    llm_adapter = None
    if _config.llm_backend != "none":
        llm = LLMCliAdapter()
        if llm.is_available:
            llm_adapter = llm
            _append_log(f"llm: {llm._cli_tool} available")

    _agent = AgentController(llm_adapter=llm_adapter)

    def on_status(msg: str):
        _append_log(msg)

    # build router (engine set after creation)
    _router = ActionRouter(
        session=_session,
        layer_manager=_layer_manager,
        engine=None,
        generator=generator,
        gateway=gateway,
        engine_registry=_engine_registry,
        engine_router=_engine_router,
        usage_tracker=usage_tracker,
        llm_adapter=llm_adapter,
        config=_config,
        session_dir=_config.session_dir,
        on_status=on_status,
    )

    _engine = _build_audio_engine(_config)
    _router.engine = _engine

    _start_audio_engine_or_fallback()
    _append_log(f"oram {__version__} ready — record to begin")

    task = asyncio.create_task(_state_broadcast_loop())

    yield

    task.cancel()
    if _router is not None:
        _router.kill_all_audio()
    if _engine is not None:
        _engine.stop()
    stop_sa3_server()


# ── security ──

_DASHBOARD_TOKEN = os.environ.get("ORAM_DASHBOARD_TOKEN", "")
_ALLOWED_ORIGINS = {"localhost", "127.0.0.1", "[::1]"}


def _get_dashboard_token() -> str:
    """return the currently configured dashboard token."""
    if _config and _config.dashboard_token:
        return _config.dashboard_token
    return _DASHBOARD_TOKEN or os.environ.get("ORAM_DASHBOARD_TOKEN", "")


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """require bearer token on mutation endpoints when ORAM_DASHBOARD_TOKEN is set."""

    async def dispatch(self, request: Request, call_next):
        token = _get_dashboard_token()
        if not token:
            return await call_next(request)
        # only guard mutation endpoints (POST)
        if request.method != "POST":
            return await call_next(request)
        # skip non-API paths
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {token}":
            return JSONResponse(
                {"error": "unauthorized", "message": "valid ORAM_DASHBOARD_TOKEN required"},
                status_code=401,
            )
        return await call_next(request)


def _is_origin_allowed(origin: str | None) -> bool:
    """check if a WebSocket origin is allowed."""
    if not origin:
        return True  # same-origin requests may omit origin
    try:
        parsed = urlparse(origin)
        host = parsed.hostname or ""
        if getattr(app.state, "allow_lan", False) and _get_dashboard_token():
            return True
        return host in _ALLOWED_ORIGINS
    except Exception:
        return False


# ── app ──

app = FastAPI(title=f"ORAM {__version__} Dashboard", lifespan=lifespan)
app.add_middleware(TokenAuthMiddleware)

STATIC_DIR = Path(__file__).parent / "static"

# mount static files for css/js
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """serve the dashboard with cache-busting."""
    html_path = STATIC_DIR / "index.html"
    html = html_path.read_text()
    # cache-bust: append timestamp to static asset URLs
    bust = str(int(time.time()))
    html = html.replace('style.css"', f'style.css?v={bust}"')
    html = html.replace('app.js"', f'app.js?v={bust}"')
    # inject dashboard token so JS can auth API calls without ?token= in URL
    token = _get_dashboard_token()
    if token:
        meta_tag = f'<meta name="oram-token" content="{token}">'
        html = html.replace('<head>', f'<head>\n{meta_tag}')
    return HTMLResponse(html)


class CommandRequest(BaseModel):
    text: str


@app.post("/api/command")
async def send_command(req: CommandRequest):
    """send a text command to the engine."""
    if _agent is None or _router is None:
        return {"error": "not initialized"}

    action = _agent.process_command(req.text)
    if action.model_dump().get("action") != "unknown":
        _push_undo(req.text[:60] or "command")
    message = _router.route(action, raw_text=req.text)
    return {"status": "ok", "message": message, "action": action.model_dump()}


@app.get("/api/state")
async def get_state():
    """get the current state snapshot. no secrets included."""
    return _get_state_snapshot()


@app.post("/api/undo")
async def undo_last_action():
    """Restore the previous audio/session snapshot."""
    if _session is None or _layer_manager is None:
        return JSONResponse({"status": "error", "message": "not initialized"}, status_code=503)
    with _history_lock:
        if not _undo_stack:
            return {"status": "ok", "message": "nothing to undo", "can_undo": False, "can_redo": bool(_redo_stack)}
        snapshot = _undo_stack.pop()
        current = _session_snapshot("redo point")
        if current is not None:
            _redo_stack.append(current)
            del _redo_stack[:-_UNDO_LIMIT]
    _restore_session_snapshot(snapshot)
    message = f"undo: {snapshot.get('label', 'last action')}"
    _append_log(message)
    return {"status": "ok", "message": message, "can_undo": bool(_undo_stack), "can_redo": bool(_redo_stack)}


@app.post("/api/redo")
async def redo_last_action():
    """Restore the next snapshot from the redo stack."""
    if _session is None or _layer_manager is None:
        return JSONResponse({"status": "error", "message": "not initialized"}, status_code=503)
    with _history_lock:
        if not _redo_stack:
            return {"status": "ok", "message": "nothing to redo", "can_undo": bool(_undo_stack), "can_redo": False}
        snapshot = _redo_stack.pop()
        current = _session_snapshot("undo point")
        if current is not None:
            _undo_stack.append(current)
            del _undo_stack[:-_UNDO_LIMIT]
    _restore_session_snapshot(snapshot)
    message = f"redo: {snapshot.get('label', 'last action')}"
    _append_log(message)
    return {"status": "ok", "message": message, "can_undo": bool(_undo_stack), "can_redo": bool(_redo_stack)}


# ── v3 engine discovery ──

@app.get("/api/engines")
async def list_engines():
    """list all registered engines and their capabilities.

    the frontend uses this to populate the engine selector
    and show available generation modes.
    """
    if _engine_registry is None:
        return {"engines": [], "capabilities": [], "providers": []}

    engines = []
    for spec in _engine_registry.list_engines():
        adapter = _engine_registry.get(spec.id)
        engines.append({
            "id": spec.id,
            "provider": spec.provider.value,
            "label": spec.label,
            "mode": spec.mode.value,
            "requires_api_key": spec.requires_api_key,
            "capabilities": [c.value for c in spec.capabilities],
            "available": adapter.is_available() if adapter else False,
            "max_duration": spec.max_duration_seconds,
            "supports_streaming": spec.supports_streaming,
            "supports_seed": spec.supports_seed,
            "supports_audio_input": spec.supports_audio_input,
            "cost_per_second": spec.cost_per_second,
            "latency_profile": spec.latency_profile,
        })

    capabilities = [c.value for c in _engine_registry.list_capabilities()]

    providers = {}
    for spec in _engine_registry.list_available_engines():
        prov = spec.provider.value
        if prov not in providers:
            providers[prov] = {"engine_count": 0, "capabilities": set()}
        providers[prov]["engine_count"] += 1
        providers[prov]["capabilities"].update(c.value for c in spec.capabilities)

    provider_list = [
        {"name": k, "engine_count": v["engine_count"], "capabilities": sorted(v["capabilities"])}
        for k, v in providers.items()
    ]

    return {
        "engines": engines,
        "capabilities": sorted(capabilities),
        "providers": provider_list,
        "total": len(engines),
        "available": _engine_registry.available_count,
    }


@app.get("/api/engines/health")
async def engine_health():
    """engine health status — availability, latency, reliability, and routing history.

    shows which engines are responsive, their average latency,
    error rates, and the last N routing decisions for transparency.
    """
    if _engine_router is None:
        return {"health": {}, "history": [], "message": "engine router not initialized"}

    health_data = {}
    for engine_id, status in _engine_router.get_health().items():
        health_data[engine_id] = {
            "available": status.available,
            "last_latency_ms": round(status.last_latency_ms, 1),
            "error_count": status.error_count,
            "success_count": status.success_count,
            "reliability": round(status.reliability, 3),
        }

    history = []
    for decision in _engine_router.get_history(limit=20):
        history.append({
            "engine_id": decision.engine_id,
            "provider": decision.provider,
            "reason": decision.reason,
            "confidence": round(decision.confidence, 2),
            "intent": decision.intent,
            "alternatives": decision.alternatives[:3],
        })

    return {
        "health": health_data,
        "history": history,
        "registry_summary": _engine_registry.summary() if _engine_registry else "",
    }


# ── v2 API routes ──

class ListenRequest(BaseModel):
    target: int | str = "selected"
    route: str = "hybrid"


@app.post("/api/listen")
async def listen_to_layer(req: ListenRequest):
    """listen to a layer through a configured route."""
    if _router is None:
        return {"error": "not initialized"}
    action = ListenAction(target=req.target, route=req.route)
    message = _router.route(action, raw_text=f"api:listen {req.route}")
    return {"status": "ok", "message": message}


class GenerateRequest(BaseModel):
    target: int | str = "selected"
    route: str = "hybrid"
    engine: str = "auto"
    duration: float | None = None


class StableAudioInpaintRange(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)


class StableAudioRenderRequest(BaseModel):
    prompt: str = Field(min_length=1)
    mode: str = "generate"
    duration: float = 8.0
    provider: str = "local"
    model: str = "stable-audio-3-local"
    decoder: str = "same-s"
    local_provider: str = "stable_audio_mlx"
    local_model: str = "sm-music"
    service_url: str = ""
    chunked_decode: bool = True
    source_layer: int | str | None = None
    target_layer: int | str | None = "first_empty"
    assign_layer: bool = True
    tags: list[str] = Field(default_factory=list)
    negative_prompt: str = ""
    seed: int | None = None
    steps: int = Field(default=8, ge=1, le=100)
    cfg_scale: float = Field(default=1.0, ge=0.0, le=20.0)
    noise_depth: float | None = Field(default=None, ge=0.0, le=1.0)
    inpaint_ranges: list[StableAudioInpaintRange] = Field(default_factory=list)
    inpaint_start: float | None = Field(default=None, ge=0.0)
    inpaint_end: float | None = Field(default=None, ge=0.0)
    variation_count: int = Field(default=1, ge=1, le=16)
    lora_a_path: str = ""
    lora_a_strength: float = Field(default=0.0, ge=0.0, le=10.0)
    lora_b_path: str = ""
    lora_b_strength: float = Field(default=0.0, ge=0.0, le=10.0)


@app.post("/api/generate")
async def generate_from_layer(req: GenerateRequest):
    """listen + compile + generate from a layer."""
    if _router is None:
        return {"error": "not initialized"}
    _push_undo("generate")
    action = GenerateFromAction(
        target=req.target,
        route=req.route,
        engine=req.engine,
        duration=req.duration,
    )
    message = _router.route(action, raw_text=f"api:generate {req.route}→{req.engine}")
    return {"status": "ok", "message": message}


@app.post("/api/stable-audio/render")
async def stable_audio_render(req: StableAudioRenderRequest):
    """render Stable Audio 3 local/API modes from the dashboard."""
    if _router is None or _layer_manager is None or _session is None or _config is None:
        return JSONResponse({"status": "error", "error": "not initialized"}, status_code=503)
    try:
        payload = await asyncio.to_thread(_stable_audio_render_sync, req)
    except ValueError as exc:
        return JSONResponse(
            {"status": "error", "error": "invalid_request", "message": redact_text(exc)},
            status_code=400,
        )
    status = 400 if payload.get("status") == "error" else 200
    return JSONResponse(payload, status_code=status)


def _stable_audio_render_sync(req: StableAudioRenderRequest) -> dict[str, Any]:
    if _router is None or _layer_manager is None or _session is None or _config is None:
        return {"status": "error", "error": "not initialized"}

    source_layer = _stable_audio_source_layer(req)
    duration = _config.validate_duration(req.duration, kind="generated")
    if req.mode == "continue" and source_layer is not None and duration <= source_layer.duration_seconds:
        duration = _config.validate_duration(source_layer.duration_seconds + req.duration, kind="generated")

    params = _stable_audio_params(req, source_layer=source_layer, duration=duration)
    engine = _select_stable_audio_engine(req.model, req.provider)
    provider = _provider_for_engine(engine, req.provider)
    audio = _router._call_engine(
        engine,
        req.prompt,
        duration,
        source_layer,
        intent=_stable_audio_intent(req.mode),
        provider=provider if req.provider != "auto" else "",
        parameters=params,
        allow_mock_fallback=False,
    )
    if audio is None:
        return {
            "status": "error",
            "error": "generation_failed",
            "message": f"Stable Audio {req.mode} failed or no SA3 engine is available",
        }

    layer_slot = None
    if req.assign_layer:
        target = _stable_audio_target_layer(req, source_layer=source_layer)
        if target is not None:
            _push_undo(f"stable audio {req.mode}")
            _layer_manager.assign_buffer(target, audio)
            target.is_generated = True
            target.source_type = SourceType.GENERATED
            target.generation_prompt = req.prompt
            target.engine_provider = provider
            target.parent_layer_id = source_layer.id if source_layer is not None else target.parent_layer_id
            target.generation_depth = (source_layer.generation_depth + 1) if source_layer is not None else 0
            layer_slot = target.slot + 1

    _session.mode = Mode.RECORD
    _append_log(f"stable audio {req.mode}: layer {layer_slot or '-'} via {provider}/{engine}")
    return {"status": "ok", "layer": layer_slot, "mode": req.mode, "engine": engine}


def _stable_audio_source_layer(req: StableAudioRenderRequest):
    if req.mode in {"generate", "lora_mixer"} and req.source_layer is None:
        return None
    target = req.source_layer or "selected"
    layer = _layer_manager.get_layer(target)
    if layer.is_empty:
        raise ValueError(f"source layer {layer.slot + 1} is empty")
    return layer


def _stable_audio_target_layer(req: StableAudioRenderRequest, *, source_layer=None):
    target = req.target_layer
    if target is None or target == "none":
        return None
    if target == "source" and source_layer is not None:
        return source_layer
    if target == "first_empty":
        return _layer_manager.find_empty_layer()
    return _layer_manager.get_layer(target)


def _stable_audio_params(req: StableAudioRenderRequest, *, source_layer=None, duration: float = 8.0) -> dict[str, Any]:
    ranges = []
    if req.inpaint_ranges:
        ranges = [
            {"start": round(r.start, 4), "end": round(r.end, 4)}
            for r in req.inpaint_ranges
            if r.end > r.start
        ]
    elif req.inpaint_start is not None and req.inpaint_end is not None:
        ranges.append({"start": req.inpaint_start, "end": req.inpaint_end})
    elif source_layer is not None and req.mode == "inpaint" and source_layer.inpaint_regions:
        for start, end in source_layer.inpaint_regions:
            ranges.append({
                "start": round(start / source_layer.sample_rate, 4),
                "end": round(end / source_layer.sample_rate, 4),
            })
    elif source_layer is not None and req.mode == "inpaint" and source_layer.looper.enabled:
        end = source_layer.looper.end_offset if source_layer.looper.end_offset > 0 else source_layer.length_samples
        ranges.append({
            "start": round(source_layer.looper.start_offset / source_layer.sample_rate, 4),
            "end": round(end / source_layer.sample_rate, 4),
        })
    elif source_layer is not None and req.mode == "continue":
        ranges.append({
            "start": round(source_layer.duration_seconds, 4),
            "end": round(duration, 4),
        })

    lora_stack = []
    if req.lora_a_path and req.lora_a_strength > 0:
        lora_stack.append({"name": "LoRA A", "path": req.lora_a_path, "strength": req.lora_a_strength})
    if req.lora_b_path and req.lora_b_strength > 0:
        lora_stack.append({"name": "LoRA B", "path": req.lora_b_path, "strength": req.lora_b_strength})

    params: dict[str, Any] = {
        "stable_audio_mode": req.mode,
        "decoder": req.decoder,
        "steps": req.steps,
        "cfg_scale": req.cfg_scale,
        "seed": req.seed,
        "negative_prompt": req.negative_prompt,
        "variation_count": req.variation_count,
        "inpaint_ranges": ranges,
        "lora_stack": lora_stack,
        "local_provider": req.local_provider,
        "local_model": req.local_model,
        "service_url": req.service_url,
        "chunked_decode": req.chunked_decode,
    }
    if source_layer is not None:
        params["source_duration"] = round(source_layer.duration_seconds, 4)
    if req.noise_depth is not None:
        params["init_noise_level"] = req.noise_depth
    return params


def _select_stable_audio_engine(model: str, provider: str) -> str:
    if model and model != "auto":
        return model
    if provider == "local":
        return "stable-audio-3-local"
    if provider == "stability":
        return "stability-stable-audio-3"
    return "stable-audio-3-local"


def _provider_for_engine(engine: str, requested: str) -> str:
    if requested and requested != "auto":
        return requested
    if engine == "stable-audio-3-local":
        return "local"
    if engine.startswith("stability"):
        return "stability"
    return "local"


def _stable_audio_intent(mode: str) -> str:
    return {
        "generate": "music",
        "lora_mixer": "music",
        "morph": "transform",
        "inpaint": "inpaint",
        "continue": "continue",
        "latent": "latent",
    }.get(mode, "sound_effect")


class ForkRequest(BaseModel):
    target: int | str = "selected"


@app.post("/api/fork")
async def fork_layer(req: ForkRequest):
    """fork a layer into an empty slot."""
    if _router is None:
        return {"error": "not initialized"}
    _push_undo("fork layer")
    action = ForkLayerAction(target=req.target)
    message = _router.route(action, raw_text="api:fork")
    return {"status": "ok", "message": message}


class SetModeRequest(BaseModel):
    target: int | str = "selected"
    mode: str  # recorder / looper / sampler


@app.post("/api/set-layer-mode")
async def set_layer_mode(req: SetModeRequest):
    """set a layer's behavior mode."""
    if _router is None:
        return {"error": "not initialized"}
    _push_undo("layer mode")
    action = SetLayerModeAction(target=req.target, mode=req.mode)
    message = _router.route(action, raw_text=f"api:mode {req.mode}")
    return {"status": "ok", "message": message}


@app.post("/api/upload-layer")
async def upload_layer(request: Request, target: int = 1, filename: str = "uploaded.wav"):
    """Import a user audio file directly into a layer."""
    if _layer_manager is None or _session is None:
        return JSONResponse({"status": "error", "error": "not_initialized"}, status_code=503)
    if target < 1 or target > len(_layer_manager.layers):
        return JSONResponse(
            {"status": "error", "error": "invalid_layer", "message": f"layer {target} not found"},
            status_code=400,
        )

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"status": "error", "error": "file_too_large", "message": "audio upload is larger than 100 MB"},
            status_code=413,
        )

    data = await request.body()
    try:
        audio, sample_rate = await asyncio.to_thread(
            decode_audio_bytes,
            data,
            target_sample_rate=_session.sample_rate,
        )
        layer = _layer_manager.get_layer(target)
        _push_undo(f"upload layer {target}")
        assign_imported_audio(_layer_manager, layer, audio, filename=filename, sample_rate=sample_rate)
    except ValueError as exc:
        return JSONResponse(
            {"status": "error", "error": "invalid_audio", "message": redact_text(exc)},
            status_code=400,
        )

    _append_log(f"uploaded {filename} → layer {target}")
    return {
        "status": "ok",
        "message": f"uploaded {filename} to layer {target}",
        "layer": target,
        "filename": filename,
        "duration": round(float(audio.shape[0]) / sample_rate, 3),
        "sample_rate": sample_rate,
    }


# ── loop region API ──

class LoopRegionRequest(BaseModel):
    target: int | str = "selected"
    start_pct: float | None = None
    end_pct: float | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    enabled: bool = True


class LoopFadeRequest(BaseModel):
    target: int | str = "selected"
    fade_in_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    fade_out_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    fade_in_seconds: float | None = Field(default=None, ge=0.0)
    fade_out_seconds: float | None = Field(default=None, ge=0.0)


class InpaintRegionPayload(BaseModel):
    start_pct: float = Field(ge=0.0, le=100.0)
    end_pct: float = Field(ge=0.0, le=100.0)


class InpaintRegionsRequest(BaseModel):
    target: int | str = "selected"
    regions: list[InpaintRegionPayload] = Field(default_factory=list)


class PlaybackReverseRequest(BaseModel):
    target: int | str = "selected"
    enabled: bool | None = None


@app.post("/api/loop-region")
async def set_loop_region(req: LoopRegionRequest):
    """set loop start/end on a layer."""
    if _router is None:
        return JSONResponse({"status": "error", "error": "not initialized"}, status_code=503)
    _push_undo("loop region")
    action = SetLoopRegionAction(
        target=req.target,
        start_pct=req.start_pct,
        end_pct=req.end_pct,
        start_seconds=req.start_seconds,
        end_seconds=req.end_seconds,
        enabled=req.enabled,
    )
    message = _router.route(action, raw_text="api:loop-region")
    ok = message.startswith("loop enabled:") or message.startswith("loop disabled:")
    if _layer_manager:
        try:
            layer = _layer_manager.get_layer(req.target)
            length = layer.length_samples
            sr = layer.sample_rate
            s = layer.looper.start_offset
            e = layer.looper.end_offset if layer.looper.end_offset > 0 else length
            payload = {
                "status": "ok" if ok else "error",
                "message": message,
                "target": layer.slot + 1,
                "loop_enabled": layer.looper.enabled,
                "loop_start_pct": round(s / length * 100, 2) if length > 0 else 0,
                "loop_end_pct": round(e / length * 100, 2) if length > 0 else 100,
                "loop_start_seconds": round(s / sr, 3) if sr > 0 else 0,
                "loop_end_seconds": round(e / sr, 3) if sr > 0 else 0,
                "loop_duration_seconds": round((e - s) / sr, 3) if sr > 0 else 0,
            }
            if not ok:
                return JSONResponse(payload, status_code=400)
            return payload
        except Exception:
            pass
    return JSONResponse({"status": "error", "message": message}, status_code=400)


@app.post("/api/loop-fades")
async def set_loop_fades(req: LoopFadeRequest):
    """set loop fade-in/fade-out lengths on a layer."""
    if _layer_manager is None:
        return JSONResponse({"status": "error", "error": "not initialized"}, status_code=503)
    try:
        layer = _layer_manager.get_layer(req.target)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": redact_text(exc)}, status_code=400)
    if layer.is_empty or layer.length_samples <= 0:
        return JSONResponse(
            {"status": "error", "message": f"layer {layer.slot + 1} is empty — cannot set loop fades"},
            status_code=400,
        )

    fade_in_samples = None
    fade_out_samples = None
    if req.fade_in_seconds is not None:
        fade_in_samples = int(req.fade_in_seconds * layer.sample_rate)
    elif req.fade_in_pct is not None:
        fade_in_samples = int((req.fade_in_pct / 100.0) * layer.length_samples)
    if req.fade_out_seconds is not None:
        fade_out_samples = int(req.fade_out_seconds * layer.sample_rate)
    elif req.fade_out_pct is not None:
        fade_out_samples = int((req.fade_out_pct / 100.0) * layer.length_samples)

    _push_undo("loop fades")
    _layer_manager.set_loop_fades(layer, fade_in_samples, fade_out_samples)
    _append_log(
        f"loop fades: layer {layer.slot + 1} "
        f"in {layer.looper.fade_in_samples / layer.sample_rate:.2f}s "
        f"out {layer.looper.fade_out_samples / layer.sample_rate:.2f}s"
    )
    return {
        "status": "ok",
        "target": layer.slot + 1,
        "fade_in_seconds": round(layer.looper.fade_in_samples / layer.sample_rate, 3),
        "fade_out_seconds": round(layer.looper.fade_out_samples / layer.sample_rate, 3),
    }


@app.post("/api/inpaint-regions")
async def set_inpaint_regions(req: InpaintRegionsRequest):
    """set Stable Audio inpaint selection regions on a layer."""
    if _layer_manager is None:
        return JSONResponse({"status": "error", "error": "not initialized"}, status_code=503)
    try:
        layer = _layer_manager.get_layer(req.target)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": redact_text(exc)}, status_code=400)
    if layer.is_empty or layer.length_samples <= 0:
        return JSONResponse(
            {"status": "error", "message": f"layer {layer.slot + 1} is empty — cannot set inpaint regions"},
            status_code=400,
        )

    regions = []
    for region in req.regions:
        lo = min(region.start_pct, region.end_pct)
        hi = max(region.start_pct, region.end_pct)
        if hi - lo < 0.1:
            continue
        regions.append((
            int((lo / 100.0) * layer.length_samples),
            int((hi / 100.0) * layer.length_samples),
        ))
    _push_undo("inpaint regions")
    _layer_manager.set_inpaint_regions(layer, regions)
    _append_log(f"inpaint regions: layer {layer.slot + 1} × {len(layer.inpaint_regions)}")
    return {
        "status": "ok",
        "target": layer.slot + 1,
        "regions": [
            {
                "start_seconds": round(start / layer.sample_rate, 3),
                "end_seconds": round(end / layer.sample_rate, 3),
            }
            for start, end in layer.inpaint_regions
        ],
    }


@app.post("/api/playback-reverse")
async def set_playback_reverse(req: PlaybackReverseRequest):
    """toggle non-destructive reverse playback for a layer."""
    if _layer_manager is None:
        return JSONResponse({"status": "error", "error": "not initialized"}, status_code=503)
    try:
        layer = _layer_manager.get_layer(req.target)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": redact_text(exc)}, status_code=400)
    if layer.is_empty:
        return JSONResponse(
            {"status": "error", "message": f"layer {layer.slot + 1} is empty — cannot reverse playback"},
            status_code=400,
        )
    enabled = (not bool(layer.reverse or layer.looper.reverse or layer.sampler.reverse)) if req.enabled is None else bool(req.enabled)
    _push_undo("reverse playback")
    _layer_manager.set_playback_reverse(layer, enabled)
    state = "on" if enabled else "off"
    _append_log(f"reverse playback {state}: layer {layer.slot + 1}")
    return {"status": "ok", "target": layer.slot + 1, "enabled": enabled}


# ── HD waveform API ──

_waveform_cache: dict[str, dict] = {}
_WAVEFORM_CACHE_MAX = 32


def _compute_waveform_peaks(layer, points: int) -> dict:
    """compute min/max peaks and RMS per bucket for a layer."""
    points = max(64, min(points, 2048))
    if layer.is_empty or layer.buffer.shape[0] == 0:
        return {
            "target": layer.slot + 1,
            "points": points,
            "revision": layer.waveform_revision,
            "duration": 0.0,
            "peaks": [],
            "rms": [],
        }
    buf = layer.buffer
    mono = np.mean(buf, axis=1) if buf.ndim > 1 else buf
    length = len(mono)
    edges = np.linspace(0, length, points + 1, dtype=int)
    peaks = []
    rms = []
    for i in range(points):
        s_idx = int(edges[i])
        e_idx = int(edges[i + 1])
        if s_idx < length and e_idx > s_idx:
            segment = mono[s_idx:e_idx]
            peaks.append([round(float(np.min(segment)), 5), round(float(np.max(segment)), 5)])
            rms.append(round(float(np.sqrt(np.mean(segment ** 2))), 5))
        else:
            peaks.append([0.0, 0.0])
            rms.append(0.0)
    return {
        "target": layer.slot + 1,
        "points": points,
        "revision": layer.waveform_revision,
        "duration": round(layer.duration_seconds, 3),
        "peaks": peaks,
        "rms": rms,
    }


@app.get("/api/waveform/{target}")
async def get_waveform(target: int, points: int = 1024):
    """get HD waveform peaks for a layer."""
    if _layer_manager is None:
        return {"error": "not initialized"}
    idx = target - 1
    if idx < 0 or idx >= len(_layer_manager.layers):
        return {"error": "invalid layer", "target": target}
    layer = _layer_manager.layers[idx]
    points = max(64, min(points, 2048))
    cache_key = f"{layer.id}:{layer.waveform_revision}:{points}"
    if cache_key in _waveform_cache:
        return _waveform_cache[cache_key]
    result = _compute_waveform_peaks(layer, points)
    if len(_waveform_cache) >= _WAVEFORM_CACHE_MAX:
        oldest = next(iter(_waveform_cache))
        del _waveform_cache[oldest]
    _waveform_cache[cache_key] = result
    return result


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """bidirectional websocket for state + commands."""
    # origin check
    origin = ws.headers.get("origin")
    if not _is_origin_allowed(origin):
        await ws.close(code=4003, reason="origin not allowed")
        return
    # token check
    dashboard_token = _get_dashboard_token()
    if dashboard_token:
        token = ws.query_params.get("token", "")
        if token != dashboard_token:
            await ws.close(code=4001, reason="unauthorized")
            return
    await ws.accept()
    _connections.append(ws)

    # send initial state
    try:
        state = _get_state_snapshot()
        await ws.send_text(json.dumps(state))
    except Exception:
        pass

    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "command" and _agent and _router:
                    text = msg.get("text", "")
                    if text:
                        action = _agent.process_command(text)
                        if action.model_dump().get("action") != "unknown":
                            _push_undo(text[:60] or "command")
                        result = _router.route(action, raw_text=text)
                        await ws.send_text(json.dumps({
                            "type": "command_result",
                            "message": result,
                            "action": action.model_dump(),
                        }))
                elif msg.get("type") == "listen" and _router:
                    target = msg.get("target", "selected")
                    route = msg.get("route", "hybrid")
                    action = ListenAction(target=target, route=route)
                    result = _router.route(action)
                    await ws.send_text(json.dumps({
                        "type": "listen_result",
                        "message": result,
                    }))
                elif msg.get("type") == "generate" and _router:
                    target = msg.get("target", "selected")
                    route = msg.get("route", "hybrid")
                    engine = msg.get("engine", "auto")
                    _push_undo("generate")
                    action = GenerateFromAction(target=target, route=route, engine=engine)
                    result = _router.route(action)
                    await ws.send_text(json.dumps({
                        "type": "generate_result",
                        "message": result,
                    }))
                elif msg.get("type") == "set_input_mode" and _session:
                    mode = msg.get("mode")
                    if mode in ("prompt", "audio"):
                        _push_undo("input mode")
                        _session.input_mode = mode
                        if _router:
                            _router.emit_status(f"input mode: {mode}")
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        if ws in _connections:
            _connections.remove(ws)


# ── recording API ──


class RecordRequest(BaseModel):
    target: int | str = "selected"
    duration: float | None = None


@app.post("/api/record")
async def start_recording(req: RecordRequest):
    """start recording from the microphone into a layer.

    when recording stops, if auto_listen is enabled, the system will
    automatically: listen → compile prompt → call ElevenLabs → create new layer.
    """
    if _engine is None or _router is None:
        return {"error": "not initialized"}

    from oram.command.schemas import RecordAction
    action = RecordAction(target=req.target, duration=req.duration)
    message = _router.route(action, raw_text="api:record")
    recording = bool(getattr(_engine, "_recording", False))
    if message.startswith("error:") or not recording:
        return JSONResponse(
            {"status": "error", "message": message, "recording": recording},
            status_code=400,
        )
    return {"status": "ok", "message": message, "recording": recording}


@app.post("/api/stop")
async def stop_recording():
    """stop recording. triggers auto-listen → ElevenLabs generate if enabled."""
    if _engine is None or _router is None:
        return {"error": "not initialized"}

    from oram.command.schemas import StopRecordingAction
    _push_undo("recording")
    action = StopRecordingAction()
    message = _router.route(action, raw_text="api:stop")
    return {"status": "ok", "message": message, "recording": bool(getattr(_engine, "_recording", False))}

@app.post("/api/kill")
async def kill_all():
    """Nuke/reset all audio and return the dashboard to startup audio state."""
    if _router is None or _layer_manager is None:
        return {"error": "not initialized"}
    _push_undo("nuke reset")
    results = _reset_to_initial_audio_state()
    msg = "reset all audio"
    _append_log(msg)
    return {"status": "ok", "message": msg, "actions": results}


@app.post("/api/auto-listen")
async def toggle_auto_listen():
    """toggle auto-listen mode (record → listen → generate)."""
    if _session is None:
        return {"error": "not initialized"}
    _push_undo("auto mode")
    _session.auto_listen = not _session.auto_listen
    return {"status": "ok", "auto_listen": _session.auto_listen}


# ── export API ──

class ExportLayerRequest(BaseModel):
    target: int = 1


@app.post("/api/export-layer")
async def export_layer(req: ExportLayerRequest):
    """export/bounce a single layer as a WAV file."""
    if _layer_manager is None or _config is None:
        return {"error": "not initialized"}

    idx = req.target - 1
    if idx < 0 or idx >= len(_layer_manager.layers):
        return {"error": "invalid layer", "message": f"layer {req.target} not found"}

    layer = _layer_manager.layers[idx]
    if layer.is_empty:
        return {"error": "empty", "message": f"layer {req.target} is empty"}

    try:
        import soundfile as sf

        export_dir = _config.session_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)

        filename = f"layer_{req.target}_{layer.name}.wav"
        filepath = export_dir / filename

        sf.write(str(filepath), layer.buffer, layer.sample_rate)

        _append_log(f"exported layer {req.target} → {filepath}")
        return {"status": "ok", "message": f"layer {req.target} exported", "path": str(filepath), "filename": filename}
    except Exception as e:
        return {"error": str(e), "message": f"export failed: {e}"}


# ── master record/export ──


class MasterRecordRequest(BaseModel):
    action: str  # "start" or "stop"


@app.post("/api/master-record")
async def master_record(req: MasterRecordRequest):
    """start/stop recording the master output."""
    if _engine is None or _config is None:
        return JSONResponse(
            {"status": "error", "message": "not initialized"},
            status_code=503,
        )

    if req.action == "start":
        if getattr(_engine, "is_master_recording", lambda: False)():
            return {
                "status": "ok",
                "recording": True,
                "elapsed": round(float(_engine.get_master_recording_seconds()), 2),
            }
        try:
            export_dir = _config.session_dir / "exports"
            filename = f"master_recording_{datetime.now().strftime('%H%M%S')}.wav"
            filepath = export_dir / filename
            _engine.start_master_recording(filepath)
        except Exception as exc:
            return JSONResponse(
                {"status": "error", "message": f"master recording failed: {redact_text(exc)}"},
                status_code=400,
            )
        _append_log(f"master recording started → {filepath}")
        return {"status": "ok", "recording": True, "path": str(filepath), "filename": filename}
    elif req.action == "stop":
        if not getattr(_engine, "is_master_recording", lambda: False)():
            return JSONResponse(
                {"status": "error", "message": "master recording is not active", "recording": False},
                status_code=400,
            )
        try:
            result = _engine.stop_master_recording()
        except Exception as exc:
            return JSONResponse(
                {"status": "error", "message": f"master recording stop failed: {redact_text(exc)}"},
                status_code=400,
            )
        path = result.get("path", "")
        duration = result.get("duration", 0.0)
        _append_log(f"master recording exported → {path} ({duration:.1f}s)")
        return {
            "status": "ok",
            "recording": False,
            "message": "master recording exported",
            **result,
        }
    return JSONResponse(
        {"status": "error", "message": "invalid master recording action"},
        status_code=400,
    )


@app.post("/api/export-master")
async def export_master():
    """export the master mix (all active layers) as a WAV file."""
    if _layer_manager is None or _config is None:
        return {"error": "not initialized"}

    try:
        from oram.audio.export import export_mix

        export_dir = _config.session_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)

        filename = f"master_mix_{datetime.now().strftime('%H%M%S')}.wav"
        filepath = export_dir / filename

        export_mix(_layer_manager, filepath, _config.sample_rate)

        _append_log(f"master mix exported → {filepath}")
        return {"status": "ok", "message": "master mix exported", "path": str(filepath)}
    except Exception as e:
        return {"error": str(e), "message": f"export failed: {e}"}


def run_server(
    host: str = "127.0.0.1",
    port: int = 3333,
    allow_lan: bool = False,
    mock_audio: bool = False,
):
    """run the dashboard server."""
    import uvicorn

    app.state.allow_lan = allow_lan or host == "0.0.0.0"
    app.state.mock_audio = mock_audio
    uvicorn.run(app, host=host, port=port, log_level="warning")


# ── audio devices & settings ──

@app.get("/api/devices")
async def list_devices():
    """list available audio input/output devices."""
    from fastapi.responses import JSONResponse

    devices = []
    default_in = -1
    default_out = -1
    try:
        import sounddevice as sd
        device_list = sd.query_devices()
        for i, dev in enumerate(device_list):
            devices.append({
                "id": int(i),
                "name": str(dev["name"]),
                "max_input_channels": int(dev["max_input_channels"]),
                "max_output_channels": int(dev["max_output_channels"]),
                "default_samplerate": float(dev["default_samplerate"]),
                "is_input": bool(dev["max_input_channels"] > 0),
                "is_output": bool(dev["max_output_channels"] > 0),
            })

        defaults = sd.default.device
        default_in = int(defaults[0]) if isinstance(defaults, (list, tuple)) else int(defaults)
        default_out = int(defaults[1]) if isinstance(defaults, (list, tuple)) else int(defaults)
    except ImportError:
        pass
    except Exception:
        pass

    def _numeric_device_id(value):
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    current_sr = int(_config.sample_rate) if _config else 48000
    current_input = None
    current_output = None
    if _engine and hasattr(_engine, "input_device") and _engine.input_device is not None:
        current_input = _numeric_device_id(_engine.input_device)
    elif _config and _config.input_device is not None:
        current_input = _numeric_device_id(_config.input_device)
    if _engine and hasattr(_engine, "output_device") and _engine.output_device is not None:
        current_output = _numeric_device_id(_engine.output_device)
    elif _config and _config.output_device is not None:
        current_output = _numeric_device_id(_config.output_device)

    return JSONResponse(content={
        "devices": devices,
        "default_input": default_in,
        "default_output": default_out,
        "current_input": current_input,
        "current_output": current_output,
        "current_sample_rate": current_sr,
        "current_format": "wav",
        "current_bit_depth": 32,
    })


class SettingsRequest(BaseModel):
    input_device: int | None = None
    output_device: int | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    rec_format: str | None = None


def _device_label(device_id: int | None, fallback: str) -> str:
    if device_id is None:
        return fallback
    try:
        import sounddevice as sd

        info = sd.query_devices(device_id)
        return f"{device_id} ({info['name']})"
    except Exception:
        return str(device_id)


def _device_supports(device_id: int | None, channel_key: str) -> bool:
    if device_id is None:
        return True
    try:
        import sounddevice as sd

        info = sd.query_devices(device_id)
        return int(info[channel_key]) > 0
    except Exception:
        return False


@app.post("/api/settings")
async def update_settings(req: SettingsRequest):
    """update audio settings. requires engine restart to take effect."""
    if _config is None:
        return {"error": "not initialized"}

    changes = []
    restart_audio = False
    requested_input = None if req.input_device == -1 else req.input_device
    requested_output = None if req.output_device == -1 else req.output_device

    if (
        req.sample_rate is not None
        and req.sample_rate in (22050, 44100, 48000, 96000)
        and req.sample_rate != _config.sample_rate
    ):
        has_audio = bool(_layer_manager and any(not layer.is_empty for layer in _layer_manager.layers))
        if has_audio:
            changes.append("sample rate unchanged — clear/export layers before changing it")
        else:
            _config.sample_rate = req.sample_rate
            if _session is not None:
                _session.sample_rate = req.sample_rate
            if _layer_manager is not None:
                _layer_manager.sample_rate = req.sample_rate
                for layer in _layer_manager.layers:
                    layer.sample_rate = req.sample_rate
            changes.append(f"sample rate → {req.sample_rate} Hz")
            restart_audio = True

    if req.bit_depth is not None and req.bit_depth in (16, 24, 32):
        changes.append(f"bit depth → {req.bit_depth}-bit")

    if req.rec_format is not None and req.rec_format in ("wav", "aiff", "flac"):
        changes.append(f"format → {req.rec_format}")

    if "input_device" in req.model_fields_set and requested_input != _config.input_device:
        if not _device_supports(requested_input, "max_input_channels"):
            return JSONResponse(
                {"status": "error", "error": "invalid_input_device", "message": "selected input has no input channels"},
                status_code=400,
            )
        _config.input_device = requested_input
        changes.append(f"input device → {_device_label(requested_input, 'system default')}")
        restart_audio = True

    if "output_device" in req.model_fields_set and requested_output != _config.output_device:
        if not _device_supports(requested_output, "max_output_channels"):
            return JSONResponse(
                {
                    "status": "error",
                    "error": "invalid_output_device",
                    "message": "selected output has no output channels",
                },
                status_code=400,
            )
        _config.output_device = requested_output
        changes.append(f"output device → {_device_label(requested_output, 'system default')}")
        restart_audio = True

    if changes:
        if restart_audio:
            restart_msg = _restart_audio_engine()
            changes.append(restart_msg)
        msg = "settings: " + ", ".join(changes)
        _append_log(msg)
        return {"status": "ok", "message": msg, "changes": changes}
    else:
        return {"status": "ok", "message": "no changes", "changes": []}


class ClearLayerRequest(BaseModel):
    target: int = 1


@app.post("/api/clear-layer")
async def clear_layer(req: ClearLayerRequest):
    """clear/delete a layer's audio content."""
    if _layer_manager is None or _router is None:
        return {"error": "not initialized"}

    from oram.command.schemas import ClearLayerAction
    _push_undo(f"clear layer {req.target}")
    action = ClearLayerAction(target=req.target, confirmed=True)
    message = _router.route(action, raw_text=f"api:clear layer {req.target}")
    return {"status": "ok", "message": message}
