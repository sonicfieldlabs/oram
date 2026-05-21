"""Local FastAPI daemon for app and plug-in control of ORAM."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from oram.agent.controller import AgentController
from oram.app import _build_gateway
from oram.audio.engine import MockAudioEngine
from oram.audio.layer import LayerManager
from oram.command.router import ActionRouter
from oram.command.schemas import (
    AnalyzeMixAction,
    ClearLayerAction,
    ExportMixAction,
    GenerateFromAction,
    GenerateLayerAction,
    RecordAction,
    SetVolumeAction,
    StopRecordingAction,
)
from oram.config import OramConfig, load_dotenv
from oram.engines.registry import EngineRegistry
from oram.engines.router import EngineRouter
from oram.gateway.usage import UsageTracker
from oram.summon.mock import MockSoundGenerator
from oram.types import LayerState, Mode, OramSession, SourceType
from oram_daemon.metadata import find_available_port, write_daemon_metadata
from oram_library import OramLibrary
from oram_security import CredentialStore, default_credential_store, redact_mapping, redact_text


def package_version() -> str:
    try:
        return version("oram")
    except PackageNotFoundError:
        return "0.0.0"


class CommandRequest(BaseModel):
    text: str = Field(min_length=1)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    duration: float = 8.0
    provider: str = "auto"
    model: str = "local-mock"
    target_layer: int | str | None = "first_empty"
    tags: list[str] = Field(default_factory=list)


class LayerTargetRequest(BaseModel):
    target: int | str = "selected"


class GenerateFromRequest(BaseModel):
    target: int | str = "selected"
    route: str = "hybrid"
    engine: str = "auto"
    duration: float | None = None
    provider: str = ""
    intent: str = "auto"


class VolumeRequest(BaseModel):
    target: int | str = "selected"
    volume: float = Field(ge=0.0, le=2.0)


class InputModeRequest(BaseModel):
    mode: str = Field(pattern="^(prompt|audio|listen)$")


class SettingsRequest(BaseModel):
    input_device: int | None = None
    output_device: int | None = None
    sample_rate: int | None = None
    block_size: int | None = None
    bit_depth: int | None = None
    rec_format: str | None = None


class RecordStartRequest(BaseModel):
    target: int | str = "selected"
    duration: float | None = None


class CredentialTestRequest(BaseModel):
    provider: str = "elevenlabs"


class FavoriteRequest(BaseModel):
    favorite: bool = True


class TagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)


class RevealRequest(BaseModel):
    sound_id: str | None = None
    path: str | None = None


class ExportRequest(BaseModel):
    sound_id: str | None = None
    format: str = "wav"


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require daemon bearer token on mutations when configured."""

    def __init__(self, app, token: str | None = None):
        super().__init__(app)
        self.token = token or ""

    async def dispatch(self, request: Request, call_next):
        if not self.token or request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self.token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


class LocalOramService:
    """Runtime state for the local daemon."""

    def __init__(
        self,
        config: OramConfig,
        *,
        library: OramLibrary | None = None,
        credential_store: CredentialStore | None = None,
        mock_audio: bool = False,
    ):
        self.config = config
        self.library = library or OramLibrary()
        self.credential_store = credential_store or default_credential_store()
        self.logs: list[str] = []

        session_name = config.session_name or f"oram_{datetime.now().strftime('%H%M%S')}"
        self.session = OramSession(
            id=session_name,
            scene=session_name,
            sample_rate=config.sample_rate,
            auto_listen=config.auto_listen,
        )
        self.layers = LayerManager(sample_rate=config.sample_rate, channels=config.channels_out)
        self.session.layers = self.layers.layers

        generator = MockSoundGenerator()
        gateway = _build_gateway(config)
        self.engine_registry = EngineRegistry.from_config(config)
        self.engine_router = (
            EngineRouter(registry=self.engine_registry, default_provider=config.preferred_provider)
            if self.engine_registry.available_count > 0
            else None
        )
        self.agent = AgentController(llm_adapter=None)
        self.engine = self._build_audio_engine(mock_audio=mock_audio or config.mock_audio)
        self.router = ActionRouter(
            session=self.session,
            layer_manager=self.layers,
            engine=self.engine,
            generator=generator,
            gateway=gateway,
            engine_registry=self.engine_registry,
            engine_router=self.engine_router,
            usage_tracker=UsageTracker(),
            llm_adapter=None,
            config=config,
            session_dir=config.session_dir,
            on_status=self.append_log,
        )
        self.engine.start()
        self.append_log("oram daemon ready")

    def refresh_provider_credentials(self) -> None:
        """Refresh provider engines after credentials are added to Keychain."""
        attrs = {
            "elevenlabs": "elevenlabs_api_key",
            "stability": "stability_api_key",
            "huggingface": "hf_token",
            "fal": "fal_key",
            "replicate": "replicate_api_token",
        }
        changed = False
        for provider, attr in attrs.items():
            try:
                value = self.credential_store.get_secret(provider) or ""
            except Exception:
                value = ""
            if getattr(self.config, attr, "") != value:
                setattr(self.config, attr, value)
                changed = True

        if not changed:
            return

        self.engine_registry = EngineRegistry.from_config(self.config)
        self.engine_router = (
            EngineRouter(registry=self.engine_registry, default_provider=self.config.preferred_provider)
            if self.engine_registry.available_count > 0
            else None
        )
        self.router.engine_registry = self.engine_registry
        self.router.engine_router = self.engine_router
        self.append_log("provider engines refreshed")

    def _build_audio_engine(self, *, mock_audio: bool):
        if not mock_audio:
            try:
                from oram.audio.realtime import RealAudioEngine

                engine = RealAudioEngine(
                    session=self.session,
                    layer_manager=self.layers,
                    sample_rate=self.config.sample_rate,
                    block_size=self.config.block_size,
                    input_device=self.config.input_device,
                    output_device=self.config.output_device,
                )
                return engine
            except Exception as exc:
                self.append_log(f"audio: real failed ({exc}), using mock")
        return MockAudioEngine(
            session=self.session,
            layer_manager=self.layers,
            sample_rate=self.config.sample_rate,
            block_size=self.config.block_size,
        )

    def append_log(self, message: str) -> None:
        self.logs.append(redact_text(message))
        if len(self.logs) > 100:
            self.logs.pop(0)

    def state(self) -> dict[str, Any]:
        layers = []
        for layer in self.layers.layers:
            if layer.is_empty:
                waveform = [0.0] * 64
            else:
                waveform = layer.waveform_data or layer.compute_waveform(64)

            playhead_pct = 0.0
            if not layer.is_empty and layer.length_samples > 0:
                playhead_pct = round((layer.playhead / layer.length_samples) * 100, 1)

            loop_end = layer.looper.end_offset if layer.looper.end_offset > 0 else layer.length_samples
            if layer.is_empty or layer.length_samples <= 0:
                loop_start_pct = 0.0
                loop_end_pct = 100.0
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
                "duration": round(layer.duration_seconds, 3),
                "muted": layer.muted,
                "solo": layer.solo,
                "volume": round(layer.volume, 3),
                "pan": round(layer.pan, 3),
                "reverse": layer.reverse,
                "speed": round(layer.speed, 2),
                "pitch_semitones": round(layer.pitch_semitones, 1),
                "effects": list(layer.effects_applied),
                "is_generated": layer.is_generated,
                "generation_prompt": layer.generation_prompt,
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
        payload = {
            "version": package_version(),
            "mode": self.session.mode.value,
            "input_mode": self.session.input_mode,
            "scene": self.session.scene,
            "sample_rate": self.session.sample_rate,
            "block_size": self.config.block_size,
            "session_dir": str(self.config.session_dir),
            "library_dir": str(self.library.root),
            "selected_layer": self.layers.selected + 1,
            "audio_running": bool(self.engine.is_running()),
            "recording": bool(getattr(self.engine, "_recording", False)),
            "input_level": round(float(getattr(self.engine, "get_input_level", lambda: 0.0)()), 3),
            "output_level": round(float(getattr(self.engine, "get_output_level", lambda: 0.0)()), 3),
            "auto_listen": self.session.auto_listen,
            "gateway": self._active_gateway_label(),
            "engine_count": self.engine_registry.available_count,
            "layers": layers,
            "log": list(self.logs[-24:]),
        }
        return redact_mapping(payload)

    def providers(self) -> dict[str, Any]:
        self.refresh_provider_credentials()
        engines = []
        for spec in self.engine_registry.list_engines():
            adapter = self.engine_registry.get(spec.id)
            engines.append({
                "id": spec.id,
                "provider": spec.provider.value,
                "label": spec.label,
                "mode": spec.mode.value,
                "requires_api_key": spec.requires_api_key,
                "available": adapter.is_available() if adapter else False,
                "capabilities": [cap.value for cap in spec.capabilities],
                "max_duration": spec.max_duration_seconds,
            })
        return {"engines": engines, "available": self.engine_registry.available_count}

    def credentials_status(self) -> dict[str, Any]:
        providers = ["elevenlabs", "stability", "huggingface", "fal", "replicate"]
        return {provider: self.credential_store.status(provider).as_dict() for provider in providers}

    def _active_gateway_label(self) -> str:
        if self.config.elevenlabs_api_key:
            return "elevenlabs"
        if self.config.stability_api_key:
            return "stability"
        if self.config.fal_key:
            return "fal"
        return "mock"

    def command(self, text: str) -> dict[str, Any]:
        action = self.agent.process_command(text)
        message = self.router.route(action, raw_text=redact_text(text))
        return redact_mapping({"status": "ok", "message": message, "action": action.model_dump()})

    def generate(self, req: GenerateRequest) -> dict[str, Any]:
        self.refresh_provider_credentials()
        duration = self.config.validate_duration(req.duration, kind="generated")
        engine = req.model or "local-mock"
        audio = self.router._call_engine(engine, req.prompt, duration, provider=req.provider)
        if audio is None:
            action = GenerateLayerAction(prompt=req.prompt, duration=duration, engine=engine)
            self.router.route(action, raw_text="daemon:generate")
            return {"status": "accepted", "message": "generation queued"}

        provider = _provider_for_engine(engine, req.provider)
        record = self.library.store_sound(
            audio,
            self.session.sample_rate,
            prompt=req.prompt,
            provider=provider,
            model=engine,
            session_id=self.session.id,
            tags=req.tags,
        )

        target = self.layers.find_empty_layer()
        if target is not None:
            self.layers.assign_buffer(target, audio)
            target.is_generated = True
            target.source_type = SourceType.GENERATED
            target.generation_prompt = req.prompt
            target.engine_provider = provider
            self.session.generated_bed_id = target.slot
            layer_slot = target.slot + 1
        else:
            layer_slot = None

        self.session.mode = Mode.RECORD
        self.append_log(f"generated {record.id} via {provider}/{engine}")
        return {
            "status": "ok",
            "sound": record.as_dict(),
            "layer": layer_slot,
        }

    def record_start(self, req: RecordStartRequest) -> dict[str, Any]:
        action = RecordAction(target=req.target, duration=req.duration)
        message = self.router.route(action, raw_text="daemon:record/start")
        return {"status": "ok", "message": message, "recording": bool(getattr(self.engine, "_recording", False))}

    def record_stop(self) -> dict[str, Any]:
        message = self.router.route(StopRecordingAction(), raw_text="daemon:record/stop")
        return {"status": "ok", "message": message, "recording": bool(getattr(self.engine, "_recording", False))}

    def clear_layer(self, req: LayerTargetRequest) -> dict[str, Any]:
        action = ClearLayerAction(target=req.target, confirmed=True)
        message = self.router.route(action, raw_text=f"daemon:clear-layer:{req.target}")
        return {"status": "ok", "message": message}

    def export_layer(self, req: LayerTargetRequest) -> dict[str, Any]:
        try:
            import soundfile as sf

            layer = self.layers.get_layer(req.target)
            if layer.is_empty:
                return {"status": "error", "error": "empty", "message": f"layer {layer.slot + 1} is empty"}

            self.library.exports_dir.mkdir(parents=True, exist_ok=True)
            filename = f"layer_{layer.slot + 1}_{layer.name}.wav"
            path = self.library.exports_dir / filename
            sf.write(str(path), layer.buffer, layer.sample_rate)
            self.append_log(f"exported layer {layer.slot + 1} -> {path}")
            return {
                "status": "ok",
                "message": f"layer {layer.slot + 1} exported",
                "path": str(path),
                "filename": filename,
            }
        except Exception as exc:
            return {
                "status": "error",
                "error": redact_text(exc),
                "message": f"export failed: {redact_text(exc)}",
            }

    def generate_from_layer(self, req: GenerateFromRequest) -> dict[str, Any]:
        action = GenerateFromAction(
            target=req.target,
            route=req.route,
            engine=req.engine,
            duration=req.duration,
            provider=req.provider,
            intent=req.intent,
        )
        message = self.router.route(action, raw_text=f"daemon:generate-from:{req.route}->{req.engine}")
        return {"status": "ok", "message": message}

    def set_volume(self, req: VolumeRequest) -> dict[str, Any]:
        action = SetVolumeAction(target=req.target, volume=req.volume)
        message = self.router.route(action, raw_text=f"daemon:volume:{req.target}:{req.volume:.3f}")
        return {"status": "ok", "message": message}

    def kill_all(self) -> dict[str, Any]:
        results = []
        if bool(getattr(self.engine, "_recording", False)):
            self.router.route(StopRecordingAction(), raw_text="daemon:kill-stop")
            results.append("stopped recording")

        for layer in self.layers.layers:
            if not layer.is_empty:
                layer.muted = True
                layer.solo = False
                layer.state = LayerState.MUTED
                results.append(f"muted layer {layer.slot + 1}")

        message = "killed all" if results else "nothing to kill"
        self.append_log(message)
        return {"status": "ok", "message": message, "actions": results}

    def set_input_mode(self, req: InputModeRequest) -> dict[str, Any]:
        if req.mode == "listen":
            self.session.input_mode = "prompt"
            self.session.auto_listen = True
        else:
            self.session.input_mode = req.mode
            self.session.auto_listen = False
        self.append_log(f"mode: {req.mode}")
        return {
            "status": "ok",
            "mode": req.mode,
            "input_mode": self.session.input_mode,
            "auto_listen": self.session.auto_listen,
        }

    def toggle_auto_listen(self) -> dict[str, Any]:
        self.session.auto_listen = not self.session.auto_listen
        self.append_log(f"auto listen: {'on' if self.session.auto_listen else 'off'}")
        return {"status": "ok", "auto_listen": self.session.auto_listen}

    def update_settings(self, req: SettingsRequest) -> dict[str, Any]:
        changes = []
        has_audio = any(not layer.is_empty for layer in self.layers.layers)

        if req.sample_rate is not None and req.sample_rate in (22050, 44100, 48000, 96000):
            if req.sample_rate != self.config.sample_rate:
                if has_audio:
                    changes.append("sample rate unchanged - clear/export layers before changing it")
                else:
                    self.config.sample_rate = req.sample_rate
                    self.session.sample_rate = req.sample_rate
                    self.layers.sample_rate = req.sample_rate
                    for layer in self.layers.layers:
                        layer.sample_rate = req.sample_rate
                    changes.append(f"sample rate -> {req.sample_rate} Hz")

        if req.block_size is not None and req.block_size in (64, 128, 256, 512, 1024, 2048):
            if req.block_size != self.config.block_size:
                self.config.block_size = req.block_size
                changes.append(f"block size -> {req.block_size}")

        if req.input_device is not None and req.input_device != self.config.input_device:
            self.config.input_device = req.input_device
            changes.append(f"input device -> {req.input_device}")

        if req.output_device is not None and req.output_device != self.config.output_device:
            self.config.output_device = req.output_device
            changes.append(f"output device -> {req.output_device}")

        if req.bit_depth is not None and req.bit_depth in (16, 24, 32):
            changes.append(f"bit depth -> {req.bit_depth}-bit")

        if req.rec_format is not None and req.rec_format in ("wav", "aiff", "flac"):
            changes.append(f"format -> {req.rec_format}")

        message = "settings: " + ", ".join(changes) if changes else "no changes"
        self.append_log(message)
        return {"status": "ok", "message": message, "changes": changes}

    def devices(self) -> dict[str, Any]:
        devices = []
        default_in = -1
        default_out = -1
        try:
            import sounddevice as sd

            for i, dev in enumerate(sd.query_devices()):
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
        except Exception:
            pass

        return {
            "devices": devices,
            "default_input": default_in,
            "default_output": default_out,
            "current_input": self.config.input_device,
            "current_output": self.config.output_device,
            "current_sample_rate": self.config.sample_rate,
            "current_format": "wav",
            "current_bit_depth": 32,
        }

    def export(self, req: ExportRequest) -> dict[str, Any]:
        if req.sound_id:
            path = self.library.export_sound(req.sound_id, fmt=req.format)
            return {"status": "ok", "path": str(path)}
        message = self.router.route(ExportMixAction(), raw_text="daemon:export")
        return {"status": "ok", "message": message}

    def analyze(self) -> dict[str, Any]:
        message = self.router.route(AnalyzeMixAction(), raw_text="daemon:analyze")
        return {"status": "ok", "message": message}

    def test_credentials(self, provider: str) -> dict[str, Any]:
        status = self.credential_store.status(provider)
        if not status.configured:
            return {"provider": provider, "configured": False, "status": "missing"}
        try:
            import httpx

            key = self.credential_store.get_secret(provider)
            if provider == "elevenlabs":
                resp = httpx.get(
                    "https://api.elevenlabs.io/v1/models",
                    headers={"xi-api-key": key or ""},
                    timeout=10.0,
                )
            elif provider == "stability":
                resp = httpx.get(
                    "https://api.stability.ai/v1/user/balance",
                    headers={"Authorization": f"Bearer {key or ''}"},
                    timeout=10.0,
                )
            else:
                return {"provider": provider, "configured": True, "status": "not_supported"}
            return {"provider": provider, "configured": True, "status": "ok" if resp.status_code < 400 else "failed"}
        except Exception as exc:
            return {"provider": provider, "configured": True, "status": "failed", "message": redact_text(exc)}

    def shutdown(self) -> None:
        self.engine.stop()


def _provider_for_engine(engine: str, requested: str) -> str:
    if requested and requested != "auto":
        return requested
    if engine.startswith("elevenlabs") or engine in {"sfx", "voice", "music"}:
        return "elevenlabs"
    if engine.startswith("stability") or engine in {"stable-audio-2", "stable-audio-2.5"}:
        return "stability"
    if engine.startswith("stable"):
        return "fal"
    return "local"


def create_app(
    service: LocalOramService,
    *,
    auth_token: str | None = None,
) -> FastAPI:
    app = FastAPI(title="ORAM Local Daemon")
    app.state.service = service
    app.add_middleware(BearerAuthMiddleware, token=auth_token)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "version": package_version(),
            "audio_running": bool(service.engine.is_running()),
        }

    @app.get("/state")
    async def state():
        return service.state()

    @app.get("/providers")
    async def providers():
        return service.providers()

    @app.get("/credentials/status")
    async def credentials_status():
        return service.credentials_status()

    @app.post("/credentials/test")
    async def credentials_test(req: CredentialTestRequest):
        return await asyncio.to_thread(service.test_credentials, req.provider)

    @app.post("/command")
    async def command(req: CommandRequest):
        return service.command(req.text)

    @app.post("/generate")
    async def generate(req: GenerateRequest):
        return await asyncio.to_thread(service.generate, req)

    @app.post("/record/start")
    async def record_start(req: RecordStartRequest):
        return service.record_start(req)

    @app.post("/record/stop")
    async def record_stop():
        return service.record_stop()

    @app.post("/layer/clear")
    async def clear_layer(req: LayerTargetRequest):
        return service.clear_layer(req)

    @app.post("/layer/export")
    async def export_layer(req: LayerTargetRequest):
        return await asyncio.to_thread(service.export_layer, req)

    @app.post("/layer/generate")
    async def generate_from_layer(req: GenerateFromRequest):
        return service.generate_from_layer(req)

    @app.post("/layer/volume")
    async def set_volume(req: VolumeRequest):
        return service.set_volume(req)

    @app.post("/kill")
    async def kill_all():
        return service.kill_all()

    @app.post("/input-mode")
    async def input_mode(req: InputModeRequest):
        return service.set_input_mode(req)

    @app.post("/auto-listen")
    async def auto_listen():
        return service.toggle_auto_listen()

    @app.get("/devices")
    async def devices():
        return service.devices()

    @app.post("/settings")
    async def settings(req: SettingsRequest):
        return service.update_settings(req)

    @app.post("/export")
    async def export(req: ExportRequest):
        return await asyncio.to_thread(service.export, req)

    @app.post("/analyze")
    async def analyze():
        return service.analyze()

    @app.get("/library")
    async def library():
        return {
            "root": str(service.library.root),
            "sessions": str(service.library.sessions_dir),
            "sounds": str(service.library.sounds_dir),
            "exports": str(service.library.exports_dir),
            "sounds_count": len(service.library.list_sounds(limit=10000)),
        }

    @app.get("/library/sounds")
    async def library_sounds(limit: int = 200):
        return {"sounds": service.library.list_sounds(limit=limit)}

    @app.get("/library/sounds/{sound_id}")
    async def library_sound(sound_id: str):
        sound = service.library.get_sound(sound_id)
        if sound is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return sound

    @app.post("/library/sounds/{sound_id}/favorite")
    async def library_favorite(sound_id: str, req: FavoriteRequest):
        sound = service.library.set_favorite(sound_id, req.favorite)
        if sound is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return sound

    @app.post("/library/sounds/{sound_id}/tags")
    async def library_tags(sound_id: str, req: TagsRequest):
        sound = service.library.set_tags(sound_id, req.tags)
        if sound is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return sound

    @app.post("/library/reveal")
    async def library_reveal(req: RevealRequest):
        try:
            path = service.library.reveal(sound_id=req.sound_id, path=req.path)
        except FileNotFoundError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return {"status": "ok", "path": str(path)}

    @app.on_event("shutdown")
    def shutdown():
        service.shutdown()

    return app


def run_daemon(
    *,
    host: str = "127.0.0.1",
    port: int | str = "auto",
    mock_audio: bool = False,
    session_dir: Path | None = None,
    auth_token: str | None = None,
) -> None:
    """Start the local daemon and write discovery metadata."""

    import uvicorn

    load_dotenv()
    config = OramConfig.from_env()
    library = OramLibrary()
    if session_dir is not None:
        config.session_dir = session_dir.expanduser()
    elif config.session_dir == Path("./oram_sessions"):
        config.session_dir = library.sessions_dir
    config.mock_audio = mock_audio or config.mock_audio

    selected_port = find_available_port(host) if str(port) == "auto" else int(port)
    token = auth_token if auth_token is not None else secrets.token_urlsafe(24)
    service = LocalOramService(config=config, library=library, mock_audio=config.mock_audio)
    app = create_app(service, auth_token=token)
    metadata_path = write_daemon_metadata(
        host=host,
        port=selected_port,
        version=package_version(),
        auth_token_configured=bool(token),
        token=token,
    )
    print(f"oram daemon listening on http://{host}:{selected_port}", flush=True)
    print(f"metadata: {metadata_path}", flush=True)
    uvicorn.run(app, host=host, port=selected_port, log_level="warning")
