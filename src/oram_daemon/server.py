"""Local FastAPI daemon for app and plug-in control of ORAM."""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from oram import __version__
from oram.agent.controller import AgentController
from oram.app import _build_gateway
from oram.audio.engine import MockAudioEngine, UnavailableAudioEngine
from oram.audio.importer import MAX_UPLOAD_BYTES, assign_imported_audio, decode_audio_bytes
from oram.audio.layer import LayerManager
from oram.command.router import ActionRouter
from oram.command.schemas import (
    AnalyzeMixAction,
    ClearLayerAction,
    ExportMixAction,
    GenerateFromAction,
    GenerateLayerAction,
    RecordAction,
    SetLoopRegionAction,
    SetVolumeAction,
    StopRecordingAction,
)
from oram.config import OramConfig, load_dotenv
from oram.engines.registry import EngineRegistry
from oram.engines.router import EngineRouter
from oram.gateway.usage import UsageTracker
from oram.summon.mock import MockSoundGenerator
from oram.types import GenerationEngine, Layer, LayerMode, LayerState, ListeningRoute, Mode, OramSession, SourceType
from oram_daemon.metadata import find_available_port, write_daemon_metadata
from oram_library import OramLibrary
from oram_security import CredentialStore, default_credential_store, redact_mapping, redact_text


def package_version() -> str:
    try:
        return version("oram")
    except PackageNotFoundError:
        return __version__


class CommandRequest(BaseModel):
    text: str = Field(min_length=1)


class ParseActionRequest(BaseModel):
    text: str = Field(min_length=1)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    duration: float = 8.0
    provider: str = "auto"
    model: str = "auto"
    target_layer: int | str | None = "first_empty"
    tags: list[str] = Field(default_factory=list)


class PluginGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    duration: float = 8.0
    provider: str = "auto"
    model: str = "auto"
    tags: list[str] = Field(default_factory=list)


class StableAudioLoraRequest(BaseModel):
    name: str = ""
    path: str = ""
    strength: float = Field(default=1.0, ge=0.0, le=10.0)
    conditioner_strength: float | None = Field(default=None, ge=0.0, le=10.0)
    interval: list[float] | None = None
    layer_filter: str = ""


class StableAudioRenderRequest(BaseModel):
    prompt: str = Field(min_length=1)
    mode: str = Field(default="generate", pattern="^(generate|morph|continue|inpaint|latent|lora_mixer)$")
    duration: float = 8.0
    provider: str = "auto"
    model: str = "auto"
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
    inpaint_start: float | None = Field(default=None, ge=0.0)
    inpaint_end: float | None = Field(default=None, ge=0.0)
    variation_count: int = Field(default=1, ge=1, le=16)
    lora_stack: list[StableAudioLoraRequest] = Field(default_factory=list)
    lora_a_path: str = ""
    lora_a_strength: float = Field(default=0.0, ge=0.0, le=10.0)
    lora_b_path: str = ""
    lora_b_strength: float = Field(default=0.0, ge=0.0, le=10.0)
    lora_interval_min: float = Field(default=0.0, ge=0.0, le=1.0)
    lora_interval_max: float = Field(default=1.0, ge=0.0, le=1.0)
    init_audio_path: str | None = None


class LayerTargetRequest(BaseModel):
    target: int | str = "selected"


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


class MasterRecordRequest(BaseModel):
    action: str = Field(pattern="^(start|stop)$")


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

    HISTORY_LIMIT = 48

    def __init__(
        self,
        config: OramConfig,
        *,
        library: OramLibrary | None = None,
        credential_store: CredentialStore | None = None,
        mock_audio: bool = False,
    ):
        self.config = config
        self.mock_audio = mock_audio or config.mock_audio
        self.library = library or OramLibrary()
        self.credential_store = credential_store or default_credential_store()
        self.logs: list[str] = []
        self.undo_stack: list[dict[str, Any]] = []
        self.redo_stack: list[dict[str, Any]] = []
        self.history_lock = threading.Lock()

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
        self.engine = self._build_audio_engine(mock_audio=self.mock_audio)
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
        self._append_audio_start_log()
        self.append_log("oram daemon ready")

    def _snapshot_layer(self, layer: Layer) -> dict[str, Any]:
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

    def _restore_layer(self, layer: Layer, snapshot: dict[str, Any]) -> None:
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

        if layer.is_empty:
            layer.waveform_data = [0.0] * 64
        else:
            layer.compute_waveform()

    def _snapshot(self, label: str) -> dict[str, Any]:
        return {
            "label": label,
            "selected": self.layers.selected,
            "session": {
                "mode": self.session.mode.value,
                "selected_layer": self.session.selected_layer,
                "listening": self.session.listening,
                "auto_listen": self.session.auto_listen,
                "input_mode": self.session.input_mode,
                "bpm": self.session.bpm,
                "generated_bed_id": self.session.generated_bed_id,
            },
            "layers": [self._snapshot_layer(layer) for layer in self.layers.layers],
        }

    def _push_undo(self, label: str) -> None:
        with self.history_lock:
            self.undo_stack.append(self._snapshot(label))
            del self.undo_stack[:-self.HISTORY_LIMIT]
            self.redo_stack.clear()

    def _restore_snapshot(self, snapshot: dict[str, Any]) -> None:
        try:
            self.router.kill_all_audio()
        except Exception:
            pass

        for layer, layer_snapshot in zip(self.layers.layers, snapshot["layers"]):
            self._restore_layer(layer, layer_snapshot)

        session_data = snapshot["session"]
        selected = max(0, min(len(self.layers.layers) - 1, int(snapshot["selected"])))
        self.layers.selected = selected
        self.session.layers = self.layers.layers
        self.session.selected_layer = int(session_data.get("selected_layer", selected))
        self.session.mode = Mode(session_data.get("mode", Mode.RECORD.value))
        self.session.listening = bool(session_data.get("listening", False))
        self.session.auto_listen = bool(session_data.get("auto_listen", False))
        self.session.input_mode = session_data.get("input_mode", "prompt")
        self.session.bpm = session_data.get("bpm")
        self.session.generated_bed_id = session_data.get("generated_bed_id")

    def _reset_to_initial_audio_state(self) -> list[str]:
        results = self.router.kill_all_audio()
        self.router._pending_clear_target = None
        self.router._pending_clear_ts = 0.0

        sample_rate = self.layers.sample_rate
        channels = self.layers.channels
        for slot, layer in enumerate(self.layers.layers):
            fresh = Layer(
                id=f"layer-{slot + 1:03d}",
                name=f"layer_{slot + 1}",
                slot=slot,
                sample_rate=sample_rate,
                channels=channels,
            )
            self._restore_layer(layer, self._snapshot_layer(fresh))

        self.layers.selected = 0
        self.session.layers = self.layers.layers
        self.session.mode = Mode.RECORD
        self.session.selected_layer = 0
        self.session.listening = False
        self.session.auto_listen = bool(self.config.auto_listen)
        self.session.input_mode = "prompt"
        self.session.bpm = None
        self.session.generated_bed_id = None
        results.append("cleared all layers")
        return results

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
        if mock_audio:
            self.append_log("audio: mock (configured)")
            return MockAudioEngine(
                session=self.session,
                layer_manager=self.layers,
                sample_rate=self.config.sample_rate,
                block_size=self.config.block_size,
            )

        try:
            from oram.audio.realtime import RealAudioEngine

            return RealAudioEngine(
                session=self.session,
                layer_manager=self.layers,
                sample_rate=self.config.sample_rate,
                block_size=self.config.block_size,
                input_device=self.config.input_device,
                output_device=self.config.output_device,
            )
        except Exception as exc:
            self.append_log(f"audio: real unavailable ({exc}); mock disabled")
            return UnavailableAudioEngine(
                reason=str(exc),
                sample_rate=self.config.sample_rate,
                block_size=self.config.block_size,
                input_device=self.config.input_device,
                output_device=self.config.output_device,
            )

    def _append_audio_start_log(self) -> None:
        if isinstance(self.engine, MockAudioEngine):
            return
        if self.engine.is_running():
            if bool(getattr(self.engine, "has_input", lambda: False)()):
                self.append_log("audio: real (input + speakers)")
            else:
                self.append_log("audio: output only (no input device)")
            return

        reason = getattr(self.engine, "reason", "hardware stream did not start")
        self.append_log(f"audio: unavailable ({reason}); mock disabled")

    def _restart_audio_engine(self) -> str:
        self.engine.stop()
        self.engine = self._build_audio_engine(mock_audio=self.mock_audio)
        self.router.engine = self.engine
        self.engine.start()
        self._append_audio_start_log()
        if not self.engine.is_running() and not isinstance(self.engine, MockAudioEngine):
            return "audio engine unavailable"
        if bool(getattr(self.engine, "has_input", lambda: True)()):
            return "audio engine restarted"
        return "audio engine restarted without input"

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
            inpaint_regions = []
            if layer.is_empty or layer.length_samples <= 0:
                loop_start_pct = 0.0
                loop_end_pct = 100.0
                loop_start_seconds = 0.0
                loop_end_seconds = 0.0
                loop_fade_in_seconds = 0.0
                loop_fade_out_seconds = 0.0
                loop_fade_in_pct = 0.0
                loop_fade_out_pct = 0.0
                loop_fade_in_loop_pct = 0.0
                loop_fade_out_loop_pct = 0.0
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
                loop_fade_in_pct = round(layer.looper.fade_in_samples / layer.length_samples * 100, 2)
                loop_fade_out_pct = round(layer.looper.fade_out_samples / layer.length_samples * 100, 2)
                loop_fade_in_loop_pct = round(layer.looper.fade_in_samples / loop_len_samples * 100, 2)
                loop_fade_out_loop_pct = round(layer.looper.fade_out_samples / loop_len_samples * 100, 2)
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
                "duration": round(layer.duration_seconds, 3),
                "muted": layer.muted,
                "solo": layer.solo,
                "volume": round(layer.volume, 3),
                "pan": round(layer.pan, 3),
                "reverse": layer.reverse,
                "playback_reverse": bool(layer.reverse or layer.looper.reverse or layer.sampler.reverse),
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
                "loop_fade_in_pct": loop_fade_in_pct,
                "loop_fade_out_pct": loop_fade_out_pct,
                "loop_fade_in_loop_pct": loop_fade_in_loop_pct,
                "loop_fade_out_loop_pct": loop_fade_out_loop_pct,
                "loop_fade_in_seconds": loop_fade_in_seconds,
                "loop_fade_out_seconds": loop_fade_out_seconds,
                "inpaint_regions": inpaint_regions,
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
            "master_recording": bool(getattr(self.engine, "is_master_recording", lambda: False)()),
            "master_recording_seconds": round(
                float(getattr(self.engine, "get_master_recording_seconds", lambda: 0.0)()),
                2,
            ),
            "input_level": round(float(getattr(self.engine, "get_input_level", lambda: 0.0)()), 3),
            "output_level": round(float(getattr(self.engine, "get_output_level", lambda: 0.0)()), 3),
            "auto_listen": self.session.auto_listen,
            "gateway": self._active_gateway_label(),
            "engine_count": self.engine_registry.available_count,
            "can_undo": bool(self.undo_stack),
            "can_redo": bool(self.redo_stack),
            "undo_label": self.undo_stack[-1]["label"] if self.undo_stack else "",
            "redo_label": self.redo_stack[-1]["label"] if self.redo_stack else "",
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
        self._push_undo(text[:60] or "command")
        message = self.router.route(action, raw_text=redact_text(text))
        return redact_mapping({"status": "ok", "message": message, "action": action.model_dump()})

    def parse_action(self, text: str) -> dict[str, Any]:
        """Parse command text without mutating daemon audio state."""
        action = self.agent.process_command(text)
        return redact_mapping({"status": "ok", "action": action.model_dump()})

    def generate(self, req: GenerateRequest) -> dict[str, Any]:
        self.refresh_provider_credentials()
        audio_epoch = self.router.audio_kill_epoch
        duration = self.config.validate_duration(req.duration, kind="generated")
        engine = req.model or "auto"
        audio = self.router._call_engine(engine, req.prompt, duration, provider=req.provider)
        if audio is None:
            self._push_undo("generate")
            action = GenerateLayerAction(prompt=req.prompt, duration=duration, engine=engine)
            self.router.route(action, raw_text="daemon:generate")
            return {"status": "accepted", "message": "generation queued"}

        if not self.router.is_audio_epoch_current(audio_epoch):
            self.append_log("generation discarded after kill")
            return {"status": "cancelled", "message": "generation discarded after kill"}

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
            self._push_undo("generate")
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

    def plugin_generate(self, req: PluginGenerateRequest) -> dict[str, Any]:
        """Generate audio for a plugin-owned layer without assigning daemon state."""
        self.refresh_provider_credentials()
        audio_epoch = self.router.audio_kill_epoch
        duration = self.config.validate_duration(req.duration, kind="generated")
        engine = req.model or "auto"
        audio = self.router._call_engine(engine, req.prompt, duration, provider=req.provider)
        if audio is None:
            return {
                "status": "error",
                "error": "generation_failed",
                "message": "generation failed: no audio returned",
            }

        if not self.router.is_audio_epoch_current(audio_epoch):
            self.append_log("plugin generation discarded after kill")
            return {"status": "cancelled", "message": "generation discarded after kill"}

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
        self.append_log(f"plugin generated {record.id} via {provider}/{engine}")
        return {
            "status": "ok",
            "sound": record.as_dict(),
        }

    def stable_audio_modes(self) -> dict[str, Any]:
        """Return SA3 modes and control metadata for apps, plugins, and Max clients."""
        return {
            "modes": [
                {
                    "id": "generate",
                    "label": "ORAM Generate",
                    "requires_source": False,
                    "controls": ["prompt", "duration", "seed", "variation_count"],
                },
                {
                    "id": "morph",
                    "label": "ORAM Morph",
                    "requires_source": True,
                    "controls": ["prompt", "duration", "seed", "noise_depth"],
                },
                {
                    "id": "continue",
                    "label": "ORAM Continue",
                    "requires_source": True,
                    "controls": ["prompt", "duration", "seed", "inpaint_range"],
                },
                {
                    "id": "inpaint",
                    "label": "ORAM Inpaint",
                    "requires_source": True,
                    "controls": ["prompt", "seed", "noise_depth", "inpaint_range"],
                },
                {
                    "id": "lora_mixer",
                    "label": "ORAM LoRA Mixer",
                    "requires_source": False,
                    "controls": ["prompt", "duration", "seed", "lora_stack", "lora_interval"],
                },
                {
                    "id": "latent",
                    "label": "ORAM Latent",
                    "requires_source": True,
                    "controls": ["prompt", "duration", "seed"],
                    "status": "sidecar_required",
                },
            ],
            "default_model": self._select_stable_audio_engine("auto", "auto"),
            "available_engines": [
                spec.id
                for spec in self.engine_registry.list_engines()
                if "stable-audio-3" in spec.id or spec.id.startswith("stability-stable-audio")
            ],
        }

    def stable_audio_render(
        self,
        req: StableAudioRenderRequest,
        *,
        plugin_owned: bool = False,
    ) -> dict[str, Any]:
        """Render an ORAM Stable Audio mode and store the returned clip."""
        self.refresh_provider_credentials()
        audio_epoch = self.router.audio_kill_epoch
        source_layer = self._stable_audio_source_layer(req)
        duration = self.config.validate_duration(req.duration, kind="generated")
        if req.mode == "continue" and source_layer is not None and duration <= source_layer.duration_seconds:
            duration = self.config.validate_duration(source_layer.duration_seconds + req.duration, kind="generated")

        params = self._stable_audio_params(req, source_layer=source_layer, duration=duration)
        engine = self._select_stable_audio_engine(req.model, req.provider)
        provider = _provider_for_engine(engine, req.provider)
        intent = _stable_audio_intent(req.mode)

        audio = self.router._call_engine(
            engine,
            req.prompt,
            duration,
            source_layer,
            intent=intent,
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

        if not self.router.is_audio_epoch_current(audio_epoch):
            self.append_log("stable audio generation discarded after kill")
            return {"status": "cancelled", "message": "generation discarded after kill"}

        tags = _stable_audio_tags(req)
        record = self.library.store_sound(
            audio,
            self.session.sample_rate,
            prompt=req.prompt,
            provider=provider,
            model=engine,
            session_id=self.session.id,
            tags=tags,
        )

        layer_slot = None
        if req.assign_layer and not plugin_owned:
            target = self._stable_audio_target_layer(req, source_layer=source_layer)
            if target is not None:
                self._push_undo(f"stable audio {req.mode}")
                self.layers.assign_buffer(target, audio)
                target.is_generated = True
                target.source_type = SourceType.GENERATED
                target.generation_prompt = req.prompt
                target.engine_provider = provider
                target.parent_layer_id = source_layer.id if source_layer is not None else target.parent_layer_id
                target.generation_depth = (source_layer.generation_depth + 1) if source_layer is not None else 0
                self.session.generated_bed_id = target.slot
                layer_slot = target.slot + 1

        self.session.mode = Mode.RECORD
        self.append_log(f"stable audio {req.mode}: {record.id} via {provider}/{engine}")
        return {
            "status": "ok",
            "sound": record.as_dict(),
            "layer": layer_slot,
            "mode": req.mode,
            "engine": engine,
        }

    def _stable_audio_source_layer(self, req: StableAudioRenderRequest):
        if req.init_audio_path:
            return None
        if req.mode in {"generate", "lora_mixer"} and req.source_layer is None:
            return None
        target = req.source_layer or "selected"
        layer = self.layers.get_layer(target)
        if layer.is_empty:
            raise ValueError(f"source layer {layer.slot + 1} is empty")
        return layer

    def _stable_audio_target_layer(self, req: StableAudioRenderRequest, *, source_layer=None):
        target = req.target_layer
        if target is None or target == "none":
            return None
        if target == "source" and source_layer is not None:
            return source_layer
        if target == "first_empty":
            return self.layers.find_empty_layer()
        return self.layers.get_layer(target)

    def _stable_audio_params(
        self,
        req: StableAudioRenderRequest,
        *,
        source_layer=None,
        duration: float,
    ) -> dict[str, Any]:
        ranges = []
        if req.inpaint_start is not None and req.inpaint_end is not None:
            ranges.append({"start": req.inpaint_start, "end": req.inpaint_end})
        elif source_layer is not None and req.mode == "inpaint" and source_layer.inpaint_regions:
            for start, end in source_layer.inpaint_regions:
                ranges.append({
                    "start": round(start / source_layer.sample_rate, 4),
                    "end": round(end / source_layer.sample_rate, 4),
                })
        elif source_layer is not None and req.mode in {"inpaint", "continue"} and source_layer.looper.enabled:
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

        lora_stack = [lora.model_dump(exclude_none=True) for lora in req.lora_stack if lora.path]
        if req.lora_a_path and req.lora_a_strength > 0:
            lora_stack.append({
                "name": "LoRA A",
                "path": req.lora_a_path,
                "strength": req.lora_a_strength,
                "interval": [req.lora_interval_min, req.lora_interval_max],
            })
        if req.lora_b_path and req.lora_b_strength > 0:
            lora_stack.append({
                "name": "LoRA B",
                "path": req.lora_b_path,
                "strength": req.lora_b_strength,
                "interval": [req.lora_interval_min, req.lora_interval_max],
            })

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
        if req.init_audio_path:
            params["init_audio_path"] = req.init_audio_path
        return params

    def _select_stable_audio_engine(self, model: str, provider: str) -> str:
        aliases = {
            "": "auto",
            "auto": "auto",
            "stable-audio-3": "stability-stable-audio-3",
            "sa3": "stable-audio-3-local",
            "local-sa3": "stable-audio-3-local",
        }
        requested = aliases.get(model, model)
        if requested != "auto":
            return requested
        if provider == "local":
            return "stable-audio-3-local"
        if provider == "stability":
            return "stability-stable-audio-3"

        local = self.engine_registry.get("stable-audio-3-local")
        if local is not None and local.is_available():
            return "stable-audio-3-local"
        stability = self.engine_registry.get("stability-stable-audio-3")
        if stability is not None and stability.is_available():
            return "stability-stable-audio-3"
        return "stable-audio-3-local"

    def record_start(self, req: RecordStartRequest) -> dict[str, Any]:
        self._push_undo("recording")
        action = RecordAction(target=req.target, duration=req.duration)
        message = self.router.route(action, raw_text="daemon:record/start")
        return {"status": "ok", "message": message, "recording": bool(getattr(self.engine, "_recording", False))}

    def record_stop(self) -> dict[str, Any]:
        message = self.router.route(StopRecordingAction(), raw_text="daemon:record/stop")
        return {"status": "ok", "message": message, "recording": bool(getattr(self.engine, "_recording", False))}

    def master_record(self, req: MasterRecordRequest) -> dict[str, Any]:
        if req.action == "start":
            if getattr(self.engine, "is_master_recording", lambda: False)():
                return {
                    "status": "ok",
                    "recording": True,
                    "elapsed": round(float(self.engine.get_master_recording_seconds()), 2),
                }
            self.config.session_dir.mkdir(parents=True, exist_ok=True)
            export_dir = self.config.session_dir / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            filename = f"master_recording_{datetime.now().strftime('%H%M%S')}.wav"
            filepath = export_dir / filename
            self.engine.start_master_recording(filepath)
            self.append_log(f"master recording started -> {filepath}")
            return {"status": "ok", "recording": True, "path": str(filepath), "filename": filename}

        if not getattr(self.engine, "is_master_recording", lambda: False)():
            return {"status": "error", "message": "master recording is not active", "recording": False}
        result = self.engine.stop_master_recording()
        path = result.get("path", "")
        duration = float(result.get("duration", 0.0))
        self.append_log(f"master recording exported -> {path} ({duration:.1f}s)")
        return {"status": "ok", "recording": False, "message": "master recording exported", **result}

    def clear_layer(self, req: LayerTargetRequest) -> dict[str, Any]:
        self._push_undo(f"clear layer {req.target}")
        action = ClearLayerAction(target=req.target, confirmed=True)
        message = self.router.route(action, raw_text=f"daemon:clear-layer:{req.target}")
        return {"status": "ok", "message": message}

    def upload_layer(self, *, target: int, filename: str, data: bytes) -> dict[str, Any]:
        layer = self.layers.get_layer(target)
        audio, sample_rate = decode_audio_bytes(data, target_sample_rate=self.session.sample_rate)
        self._push_undo(f"upload layer {target}")
        assign_imported_audio(self.layers, layer, audio, filename=filename, sample_rate=sample_rate)
        self.append_log(f"uploaded {filename} -> layer {target}")
        return {
            "status": "ok",
            "message": f"uploaded {filename} to layer {target}",
            "layer": target,
            "filename": filename,
            "duration": round(float(audio.shape[0]) / sample_rate, 3),
            "sample_rate": sample_rate,
        }

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
        self._push_undo("generate from layer")
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

    def set_loop_region(self, req: LoopRegionRequest) -> dict[str, Any]:
        self._push_undo("loop region")
        action = SetLoopRegionAction(
            target=req.target,
            start_pct=req.start_pct,
            end_pct=req.end_pct,
            start_seconds=req.start_seconds,
            end_seconds=req.end_seconds,
            enabled=req.enabled,
        )
        message = self.router.route(action, raw_text="daemon:loop-region")
        ok = message.startswith("loop enabled:") or message.startswith("loop disabled:")
        try:
            layer = self.layers.get_layer(req.target)
            length = layer.length_samples
            sr = layer.sample_rate
            start = layer.looper.start_offset
            end = layer.looper.end_offset if layer.looper.end_offset > 0 else length
            payload = {
                "status": "ok" if ok else "error",
                "message": message,
                "target": layer.slot + 1,
                "loop_enabled": layer.looper.enabled,
                "loop_start_pct": round(start / length * 100, 2) if length > 0 else 0.0,
                "loop_end_pct": round(end / length * 100, 2) if length > 0 else 100.0,
                "loop_start_seconds": round(start / sr, 3) if sr > 0 else 0.0,
                "loop_end_seconds": round(end / sr, 3) if sr > 0 else 0.0,
                "loop_duration_seconds": round((end - start) / sr, 3) if sr > 0 else 0.0,
            }
        except Exception as exc:
            return {
                "status": "error",
                "error": redact_text(exc),
                "message": message,
            }
        return payload

    def waveform(self, target: int, points: int = 1024) -> dict[str, Any]:
        points = max(64, min(int(points), 2048))
        try:
            layer = self.layers.get_layer(target)
        except Exception as exc:
            return {"error": "invalid layer", "message": redact_text(exc), "target": target}

        if layer.is_empty or layer.buffer.shape[0] == 0:
            return {
                "target": layer.slot + 1,
                "points": points,
                "revision": layer.waveform_revision,
                "duration": 0.0,
                "peaks": [],
                "rms": [],
            }

        with layer._buf_lock:
            buffer = np.array(layer.buffer, copy=True)
        mono = np.mean(buffer, axis=1) if buffer.ndim > 1 else buffer
        length = len(mono)
        edges = np.linspace(0, length, points + 1, dtype=int)
        peaks = []
        rms = []
        for index in range(points):
            start = int(edges[index])
            end = int(edges[index + 1])
            if start < length and end > start:
                segment = mono[start:end]
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

    def set_volume(self, req: VolumeRequest) -> dict[str, Any]:
        self._push_undo(f"volume layer {req.target}")
        action = SetVolumeAction(target=req.target, volume=req.volume)
        message = self.router.route(action, raw_text=f"daemon:volume:{req.target}:{req.volume:.3f}")
        return {"status": "ok", "message": message}

    def kill_all(self) -> dict[str, Any]:
        self._push_undo("nuke reset")
        results = self._reset_to_initial_audio_state()
        message = "reset all audio"
        self.append_log(message)
        return {"status": "ok", "message": message, "actions": results}

    def undo(self) -> dict[str, Any]:
        with self.history_lock:
            if not self.undo_stack:
                return {
                    "status": "ok",
                    "message": "nothing to undo",
                    "can_undo": False,
                    "can_redo": bool(self.redo_stack),
                }
            snapshot = self.undo_stack.pop()
            self.redo_stack.append(self._snapshot("redo point"))
            del self.redo_stack[:-self.HISTORY_LIMIT]
        self._restore_snapshot(snapshot)
        message = f"undo: {snapshot.get('label', 'last action')}"
        self.append_log(message)
        return {"status": "ok", "message": message, "can_undo": bool(self.undo_stack), "can_redo": bool(self.redo_stack)}

    def redo(self) -> dict[str, Any]:
        with self.history_lock:
            if not self.redo_stack:
                return {
                    "status": "ok",
                    "message": "nothing to redo",
                    "can_undo": bool(self.undo_stack),
                    "can_redo": False,
                }
            snapshot = self.redo_stack.pop()
            self.undo_stack.append(self._snapshot("undo point"))
            del self.undo_stack[:-self.HISTORY_LIMIT]
        self._restore_snapshot(snapshot)
        message = f"redo: {snapshot.get('label', 'last action')}"
        self.append_log(message)
        return {"status": "ok", "message": message, "can_undo": bool(self.undo_stack), "can_redo": bool(self.redo_stack)}

    def set_loop_fades(self, req: LoopFadeRequest) -> dict[str, Any]:
        layer = self.layers.get_layer(req.target)
        if layer.is_empty or layer.length_samples <= 0:
            return {"status": "error", "message": f"layer {layer.slot + 1} is empty - cannot set loop fades"}

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

        self._push_undo("loop fades")
        self.layers.set_loop_fades(layer, fade_in_samples, fade_out_samples)
        self.append_log(
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

    def set_inpaint_regions(self, req: InpaintRegionsRequest) -> dict[str, Any]:
        layer = self.layers.get_layer(req.target)
        if layer.is_empty or layer.length_samples <= 0:
            return {"status": "error", "message": f"layer {layer.slot + 1} is empty - cannot set inpaint regions"}

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

        self._push_undo("inpaint regions")
        self.layers.set_inpaint_regions(layer, regions)
        self.append_log(f"inpaint regions: layer {layer.slot + 1} x {len(layer.inpaint_regions)}")
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

    def set_playback_reverse(self, req: PlaybackReverseRequest) -> dict[str, Any]:
        layer = self.layers.get_layer(req.target)
        if layer.is_empty:
            return {"status": "error", "message": f"layer {layer.slot + 1} is empty - cannot reverse playback"}
        enabled = (not bool(layer.reverse or layer.looper.reverse or layer.sampler.reverse)) if req.enabled is None else bool(req.enabled)
        self._push_undo("reverse playback")
        self.layers.set_playback_reverse(layer, enabled)
        state = "on" if enabled else "off"
        self.append_log(f"reverse playback {state}: layer {layer.slot + 1}")
        return {"status": "ok", "target": layer.slot + 1, "enabled": enabled}

    def set_input_mode(self, req: InputModeRequest) -> dict[str, Any]:
        self._push_undo("input mode")
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
        self._push_undo("auto listen")
        self.session.auto_listen = not self.session.auto_listen
        self.append_log(f"auto listen: {'on' if self.session.auto_listen else 'off'}")
        return {"status": "ok", "auto_listen": self.session.auto_listen}

    def update_settings(self, req: SettingsRequest) -> dict[str, Any]:
        changes = []
        has_audio = any(not layer.is_empty for layer in self.layers.layers)
        restart_audio = False

        requested_input = None if req.input_device == -1 else req.input_device
        requested_output = None if req.output_device == -1 else req.output_device

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
                    restart_audio = True

        if req.block_size is not None and req.block_size in (64, 128, 256, 512, 1024, 2048):
            if req.block_size != self.config.block_size:
                self.config.block_size = req.block_size
                changes.append(f"block size -> {req.block_size}")
                restart_audio = True

        if "input_device" in req.model_fields_set and requested_input != self.config.input_device:
            self.config.input_device = requested_input
            changes.append(f"input device -> {requested_input if requested_input is not None else 'system default'}")
            restart_audio = True

        if "output_device" in req.model_fields_set and requested_output != self.config.output_device:
            self.config.output_device = requested_output
            changes.append(f"output device -> {requested_output if requested_output is not None else 'system default'}")
            restart_audio = True

        if req.bit_depth is not None and req.bit_depth in (16, 24, 32):
            changes.append(f"bit depth -> {req.bit_depth}-bit")

        if req.rec_format is not None and req.rec_format in ("wav", "aiff", "flac"):
            changes.append(f"format -> {req.rec_format}")

        if restart_audio:
            changes.append(self._restart_audio_engine())

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
        self.router.kill_all_audio()
        self.engine.stop()


def _provider_for_engine(engine: str, requested: str) -> str:
    if requested and requested != "auto":
        return requested
    if engine.startswith("elevenlabs") or engine in {"sfx", "voice", "music"}:
        return "elevenlabs"
    if engine == "stable-audio-3-local":
        return "local"
    if engine.startswith("stability") or engine in {"stable-audio-2", "stable-audio-2.5", "stable-audio-3"}:
        return "stability"
    if engine.startswith("stable"):
        return "fal"
    return "local"


def _stable_audio_intent(mode: str) -> str:
    if mode == "generate" or mode == "lora_mixer":
        return "music"
    if mode == "morph":
        return "transform"
    if mode == "inpaint":
        return "inpaint"
    if mode == "continue":
        return "continue"
    if mode == "latent":
        return "latent"
    return "sound_effect"


def _stable_audio_tags(req: StableAudioRenderRequest) -> list[str]:
    tags = {tag.strip() for tag in req.tags if tag.strip()}
    tags.add("stable-audio")
    tags.add(f"mode:{req.mode}")
    if req.lora_stack or req.lora_a_path or req.lora_b_path:
        tags.add("lora")
    return sorted(tags)


def create_app(
    service: LocalOramService,
    *,
    auth_token: str | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.service.shutdown()
        from oram.engines.sa3_launcher import stop_sa3_server
        stop_sa3_server()

    app = FastAPI(title="ORAM Local Daemon", lifespan=lifespan)
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

    @app.post("/actions/parse")
    async def parse_action(req: ParseActionRequest):
        return service.parse_action(req.text)

    @app.post("/generate")
    async def generate(req: GenerateRequest):
        return await asyncio.to_thread(service.generate, req)

    @app.post("/plugin/generate")
    async def plugin_generate(req: PluginGenerateRequest):
        return await asyncio.to_thread(service.plugin_generate, req)

    @app.get("/stable-audio/modes")
    async def stable_audio_modes():
        return service.stable_audio_modes()

    @app.post("/stable-audio/render")
    async def stable_audio_render(req: StableAudioRenderRequest):
        try:
            payload = await asyncio.to_thread(service.stable_audio_render, req)
        except ValueError as exc:
            return JSONResponse(
                {"status": "error", "error": "invalid_request", "message": redact_text(exc)},
                status_code=400,
            )
        status = 400 if payload.get("status") == "error" else 200
        return JSONResponse(payload, status_code=status)

    @app.post("/plugin/stable-audio/render")
    async def plugin_stable_audio_render(req: StableAudioRenderRequest):
        try:
            payload = await asyncio.to_thread(service.stable_audio_render, req, plugin_owned=True)
        except ValueError as exc:
            return JSONResponse(
                {"status": "error", "error": "invalid_request", "message": redact_text(exc)},
                status_code=400,
            )
        status = 400 if payload.get("status") == "error" else 200
        return JSONResponse(payload, status_code=status)

    @app.post("/record/start")
    async def record_start(req: RecordStartRequest):
        return service.record_start(req)

    @app.post("/record/stop")
    async def record_stop():
        return service.record_stop()

    @app.post("/master-record")
    async def master_record(req: MasterRecordRequest):
        try:
            payload = service.master_record(req)
        except Exception as exc:
            return JSONResponse(
                {"status": "error", "message": f"master recording failed: {redact_text(exc)}"},
                status_code=400,
            )
        if payload.get("status") == "error":
            return JSONResponse(payload, status_code=400)
        return payload

    @app.post("/layer/clear")
    async def clear_layer(req: LayerTargetRequest):
        return service.clear_layer(req)

    @app.post("/layer/upload")
    async def upload_layer(request: Request, target: int = 1, filename: str = "uploaded.wav"):
        if target < 1 or target > len(service.layers.layers):
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
        try:
            data = await request.body()
            payload = await asyncio.to_thread(service.upload_layer, target=target, filename=filename, data=data)
        except ValueError as exc:
            return JSONResponse(
                {"status": "error", "error": "invalid_audio", "message": redact_text(exc)},
                status_code=400,
            )
        return payload

    @app.post("/layer/export")
    async def export_layer(req: LayerTargetRequest):
        return await asyncio.to_thread(service.export_layer, req)

    @app.post("/layer/generate")
    async def generate_from_layer(req: GenerateFromRequest):
        return service.generate_from_layer(req)

    @app.post("/layer/loop-region")
    async def loop_region(req: LoopRegionRequest):
        payload = service.set_loop_region(req)
        if payload.get("status") == "error":
            return JSONResponse(payload, status_code=400)
        return payload

    @app.post("/layer/loop-fades")
    async def loop_fades(req: LoopFadeRequest):
        try:
            payload = service.set_loop_fades(req)
        except Exception as exc:
            return JSONResponse({"status": "error", "message": redact_text(exc)}, status_code=400)
        if payload.get("status") == "error":
            return JSONResponse(payload, status_code=400)
        return payload

    @app.post("/layer/inpaint-regions")
    async def inpaint_regions(req: InpaintRegionsRequest):
        try:
            payload = service.set_inpaint_regions(req)
        except Exception as exc:
            return JSONResponse({"status": "error", "message": redact_text(exc)}, status_code=400)
        if payload.get("status") == "error":
            return JSONResponse(payload, status_code=400)
        return payload

    @app.post("/layer/playback-reverse")
    async def playback_reverse(req: PlaybackReverseRequest):
        try:
            payload = service.set_playback_reverse(req)
        except Exception as exc:
            return JSONResponse({"status": "error", "message": redact_text(exc)}, status_code=400)
        if payload.get("status") == "error":
            return JSONResponse(payload, status_code=400)
        return payload

    @app.get("/waveform/{target}")
    async def waveform(target: int, points: int = 1024):
        return service.waveform(target=target, points=points)

    @app.post("/layer/volume")
    async def set_volume(req: VolumeRequest):
        return service.set_volume(req)

    @app.post("/kill")
    async def kill_all():
        return service.kill_all()

    @app.post("/undo")
    async def undo():
        return service.undo()

    @app.post("/redo")
    async def redo():
        return service.redo()

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

    @app.websocket("/ws")
    async def websocket_state(ws: WebSocket):
        if auth_token:
            query_token = ws.query_params.get("token", "")
            auth_header = ws.headers.get("authorization", "")
            if query_token != auth_token and auth_header != f"Bearer {auth_token}":
                await ws.close(code=4001, reason="unauthorized")
                return

        await ws.accept()
        try:
            while True:
                await ws.send_text(json.dumps(service.state()))
                await asyncio.sleep(1 / 12)
        except WebSocketDisconnect:
            return
        except RuntimeError:
            return

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
    from oram.engines.sa3_launcher import start_sa3_server

    sa3_url = start_sa3_server()

    load_dotenv()
    config = OramConfig.from_env()
    library = OramLibrary()
    if sa3_url and not os.environ.get("PYTEST_CURRENT_TEST"):
        config.stable_audio_service_url = sa3_url
    if session_dir is not None:
        config.session_dir = session_dir.expanduser()
    elif config.session_dir == Path("./oram_sessions"):
        config.session_dir = library.sessions_dir

    if os.environ.get("PYTEST_CURRENT_TEST"):
        config.mock_audio = mock_audio or config.mock_audio
    else:
        config.mock_audio = False

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
        project_path=str(Path.cwd()),
    )
    print(f"oram daemon listening on http://{host}:{selected_port}", flush=True)
    print(f"metadata: {metadata_path}", flush=True)
    uvicorn.run(app, host=host, port=selected_port, log_level="warning")
