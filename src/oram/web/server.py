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
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from oram.agent.controller import AgentController
from oram.agent.llm_adapter import LLMCliAdapter
from oram.audio.engine import MockAudioEngine
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
from oram.types import OramSession
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

        layers.append({
            "id": layer.id,
            "slot": layer.slot + 1,
            "name": layer.name,
            "state": layer.state.value,
            "source_type": layer.source_type.value,
            "layer_mode": layer.layer_mode.value,
            "duration": round(layer.duration_seconds, 2),
            "volume": round(layer.volume, 2),
            "pan": round(layer.pan, 2),
            "muted": layer.muted,
            "solo": layer.solo,
            "reverse": layer.reverse,
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
        "version": "2.0.0",
        "mode": _session.mode.value,
        "input_mode": _session.input_mode,
        "scene": _session.scene,
        "sample_rate": _config.sample_rate if _config else _session.sample_rate,
        "bpm": _session.bpm,
        "selected_layer": _layer_manager.selected,
        "input_level": round(_engine.get_input_level(), 3),
        "output_level": round(_engine.get_output_level(), 3),
        "recording": bool(getattr(_engine, "_recording", False)),
        "input_available": bool(getattr(_engine, "has_input", lambda: True)()),
        "audio_running": bool(_engine.is_running()),
        "auto_listen": _session.auto_listen,
        "gateway": gateway_status,
        "engines": engines_info,
        "engine_count": _engine_registry.available_count if _engine_registry else 0,
        "layers": layers,
        "log": list(_log_messages[-16:]),
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

    _append_log(f"recorded layer {layer.slot + 1} — listening...")

    try:
        from oram.ears.prompt_compiler import compile_prompt
        from oram.ears.routes import create_route
        from oram.gateway.router import select_engine

        route = create_route("hybrid", llm_adapter=_router.llm_adapter)
        report = route.listen(layer.buffer, layer.sample_rate)
        _router._listening_reports[layer.id] = report

        tech = report.technical
        parts = []
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

        prompt = compile_prompt(report, decision.engine)
        _append_log(f"prompt: {prompt[:80]}...")

        gen_duration = min(layer.duration_seconds * 1.2, 30.0)
        audio = _router._call_engine(decision.engine, prompt, gen_duration, layer)

        if audio is None:
            _append_log("generation: no audio returned (check API key)")
            return

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
    """build the active audio engine for the dashboard."""
    use_mock = getattr(app.state, "mock_audio", False) or config.mock_audio
    if not use_mock:
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
            _append_log(f"audio: real failed ({e}), using mock")

    _append_log("audio: mock (no hardware)")
    return MockAudioEngine(
        session=_session,
        layer_manager=_layer_manager,
        sample_rate=config.sample_rate,
        block_size=config.block_size,
        on_record_complete=_on_record_complete,
    )


def _start_audio_engine_or_fallback() -> None:
    """start the current engine and fall back to mock if hardware startup fails."""
    global _engine
    if _engine is None or _config is None:
        return

    _engine.start()
    if _engine.is_running():
        if isinstance(_engine, MockAudioEngine):
            return
        if bool(getattr(_engine, "has_input", lambda: False)()):
            _append_log("audio: real (input + speakers)")
        else:
            _append_log("audio: output only (no input device)")
        return

    _append_log("audio: hardware failed to start, using mock")
    _engine = MockAudioEngine(
        session=_session,
        layer_manager=_layer_manager,
        sample_rate=_config.sample_rate,
        block_size=_config.block_size,
        on_record_complete=_on_record_complete,
    )
    _engine.start()
    _append_log("audio: mock (no hardware)")


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
    if bool(getattr(_engine, "has_input", lambda: True)()):
        return "audio engine restarted"
    return "audio engine restarted without input"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """initialize ORAM v2 engine on startup, clean up on shutdown."""
    global _session, _layer_manager, _engine, _router, _agent, _config, _DASHBOARD_TOKEN
    global _engine_registry, _engine_router

    load_dotenv()
    _config = OramConfig.from_env()
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
    _append_log("oram v2 ready — record to begin")

    task = asyncio.create_task(_state_broadcast_loop())

    yield

    task.cancel()
    _engine.stop()


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

app = FastAPI(title="oram v2", lifespan=lifespan)
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
    message = _router.route(action, raw_text=req.text)
    return {"status": "ok", "message": message, "action": action.model_dump()}


@app.get("/api/state")
async def get_state():
    """get the current state snapshot. no secrets included."""
    return _get_state_snapshot()


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


@app.post("/api/generate")
async def generate_from_layer(req: GenerateRequest):
    """listen + compile + generate from a layer."""
    if _router is None:
        return {"error": "not initialized"}
    action = GenerateFromAction(
        target=req.target,
        route=req.route,
        engine=req.engine,
        duration=req.duration,
    )
    message = _router.route(action, raw_text=f"api:generate {req.route}→{req.engine}")
    return {"status": "ok", "message": message}


class ForkRequest(BaseModel):
    target: int | str = "selected"


@app.post("/api/fork")
async def fork_layer(req: ForkRequest):
    """fork a layer into an empty slot."""
    if _router is None:
        return {"error": "not initialized"}
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
    action = SetLayerModeAction(target=req.target, mode=req.mode)
    message = _router.route(action, raw_text=f"api:mode {req.mode}")
    return {"status": "ok", "message": message}


# ── loop region API ──

class LoopRegionRequest(BaseModel):
    target: int | str = "selected"
    start_pct: float | None = None
    end_pct: float | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    enabled: bool = True


@app.post("/api/loop-region")
async def set_loop_region(req: LoopRegionRequest):
    """set loop start/end on a layer."""
    if _router is None:
        return JSONResponse({"status": "error", "error": "not initialized"}, status_code=503)
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
    chunk = max(1, length // points)
    peaks = []
    rms = []
    for i in range(points):
        s_idx = i * chunk
        e_idx = min(s_idx + chunk, length)
        if s_idx < length:
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
                    action = GenerateFromAction(target=target, route=route, engine=engine)
                    result = _router.route(action)
                    await ws.send_text(json.dumps({
                        "type": "generate_result",
                        "message": result,
                    }))
                elif msg.get("type") == "set_input_mode" and _session:
                    mode = msg.get("mode")
                    if mode in ("prompt", "audio"):
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
    action = StopRecordingAction()
    message = _router.route(action, raw_text="api:stop")
    return {"status": "ok", "message": message, "recording": bool(getattr(_engine, "_recording", False))}

@app.post("/api/kill")
async def kill_all():
    """kill all sound: stop recording + mute all layers."""
    results = []
    if _engine and _engine._recording:
        from oram.command.schemas import StopRecordingAction
        _router.route(StopRecordingAction(), raw_text="api:kill-stop")
        results.append("stopped recording")

    if _layer_manager:
        from oram.command.schemas import MuteLayerAction
        for i, layer in enumerate(_layer_manager.layers):
            if not layer.is_empty and not layer.muted:
                action = MuteLayerAction(target=i + 1)
                _router.route(action, raw_text=f"api:kill-mute-{i+1}")
                results.append(f"muted layer {i + 1}")

    msg = "killed all" if results else "nothing to kill"
    _append_log(msg)
    return {"status": "ok", "message": msg, "actions": results}


@app.post("/api/auto-listen")
async def toggle_auto_listen():
    """toggle auto-listen mode (record → listen → generate)."""
    if _session is None:
        return {"error": "not initialized"}
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

_master_recording = False
_master_buffer: list = []


class MasterRecordRequest(BaseModel):
    action: str  # "start" or "stop"


@app.post("/api/master-record")
async def master_record(req: MasterRecordRequest):
    """start/stop recording the master output."""
    global _master_recording, _master_buffer

    if req.action == "start":
        _master_recording = True
        _master_buffer = []
        _append_log("master recording started")
        return {"status": "ok", "recording": True}
    elif req.action == "stop":
        _master_recording = False
        _append_log(f"master recording stopped ({len(_master_buffer)} blocks)")
        return {"status": "ok", "recording": False, "blocks": len(_master_buffer)}
    else:
        return {"error": "invalid action"}


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


def run_server(host: str = "127.0.0.1", port: int = 3333, mock_audio: bool = False, allow_lan: bool = False):
    """run the dashboard server."""
    import uvicorn

    app.state.mock_audio = mock_audio
    app.state.allow_lan = allow_lan or host == "0.0.0.0"
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

    current_sr = int(_config.sample_rate) if _config else 48000
    current_input = None
    if _engine and hasattr(_engine, "input_device") and _engine.input_device is not None:
        current_input = int(_engine.input_device)

    return JSONResponse(content={
        "devices": devices,
        "default_input": default_in,
        "default_output": default_out,
        "current_input": current_input,
        "current_sample_rate": current_sr,
        "current_format": "wav",
        "current_bit_depth": 32,
    })


class SettingsRequest(BaseModel):
    input_device: int | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    rec_format: str | None = None


@app.post("/api/settings")
async def update_settings(req: SettingsRequest):
    """update audio settings. requires engine restart to take effect."""
    if _config is None:
        return {"error": "not initialized"}

    changes = []
    restart_audio = False

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

    if req.input_device is not None and req.input_device != _config.input_device:
        _config.input_device = req.input_device
        changes.append(f"input device → {req.input_device}")
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
    action = ClearLayerAction(target=req.target, confirmed=True)
    message = _router.route(action, raw_text=f"api:clear layer {req.target}")
    return {"status": "ok", "message": message}
