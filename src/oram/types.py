"""oram core types — data models for layers, sessions, modes, and commands.

oram v2: layers are the central primitive. each layer is a playable sonic
object with memory, behavior, and listening history.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import numpy as np


class Mode(str, Enum):
    """oram operating modes."""

    RECORD = "record"
    LISTEN = "listen"
    LOOP = "loop"
    SHAPE = "shape"
    SUMMON = "summon"
    SLEEP = "sleep"


class LayerState(str, Enum):
    """lifecycle state of a layer."""

    EMPTY = "empty"
    RECORDING = "recording"
    ACTIVE = "active"
    MUTED = "muted"


class SourceType(str, Enum):
    """how a layer's audio was created."""

    RECORDED = "recorded"
    GENERATED = "generated"
    IMPORTED = "imported"
    RESAMPLED = "resampled"


class LayerMode(str, Enum):
    """per-layer behavior mode."""

    RECORDER = "recorder"
    LOOPER = "looper"
    SAMPLER = "sampler"


class ListeningRoute(str, Enum):
    """listening analysis route."""

    TECHNICAL = "technical"
    DESCRIPTIVE = "descriptive"
    SPECULATIVE = "speculative"
    HYBRID = "hybrid"


class GenerationEngine(str, Enum):
    """generation engine — supports both legacy mode names and provider-specific IDs."""

    AUTO = "auto"
    # legacy mode names (backward compat)
    SFX = "sfx"
    VOICE = "voice"
    MUSIC = "music"
    # provider-specific engine IDs
    ELEVENLABS_SFX = "elevenlabs-sfx"
    ELEVENLABS_TTS = "elevenlabs-tts"
    ELEVENLABS_MUSIC = "elevenlabs-music"
    ELEVENLABS_SCRIBE = "elevenlabs-scribe"
    STABLE_AUDIO = "stable-audio-25"
    STABILITY_STABLE_AUDIO_25 = "stability-stable-audio-25"
    STABILITY_STABLE_AUDIO_3 = "stability-stable-audio-3"
    STABLE_AUDIO_3_LOCAL = "stable-audio-3-local"
    LOCAL = "local"
    LOCAL_MOCK = "local-mock"


@dataclass
class ADSREnvelope:
    """attack-decay-sustain-release envelope for sampler."""

    attack: float = 0.01
    decay: float = 0.1
    sustain: float = 0.8
    release: float = 0.3


@dataclass
class LooperParams:
    """per-layer looper parameters."""

    enabled: bool = False
    sync_to_master: bool = False
    free_loop: bool = True
    start_offset: int = 0
    end_offset: int = 0  # 0 means end of buffer
    fade_in_samples: int = 0
    fade_out_samples: int = 0
    reverse: bool = False
    half_speed: bool = False
    double_speed: bool = False


@dataclass
class SamplerParams:
    """per-layer sampler parameters."""

    root_note: int = 60  # MIDI note C3
    mode: str = "one_shot"  # one_shot / gate / loop
    adsr: ADSREnvelope = field(default_factory=ADSREnvelope)
    start_point: int = 0
    end_point: int = 0  # 0 means end of buffer
    reverse: bool = False
    transpose: int = 0
    fine_tune: float = 0.0
    polyphony: int = 4
    velocity_sensitivity: bool = True


@dataclass
class Layer:
    """a layer is a playable sonic object with memory, behavior, and listening history.

    the central primitive of oram v2.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    slot: int = 0  # 0-based display position

    # source
    source_type: SourceType = SourceType.RECORDED

    # audio
    buffer: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.float32))
    waveform_data: list[float] = field(default_factory=list)
    waveform_revision: int = 0
    sample_rate: int = 48000
    channels: int = 2
    duration_seconds: float = 0.0

    # playback
    playhead: int = 0
    volume: float = 1.0
    pan: float = 0.0
    muted: bool = False
    solo: bool = False
    state: LayerState = LayerState.EMPTY

    # per-layer mode
    layer_mode: LayerMode = LayerMode.RECORDER

    # looper
    looper: LooperParams = field(default_factory=LooperParams)

    # sampler
    sampler: SamplerParams = field(default_factory=SamplerParams)

    # dsp state (carried from v1)
    reverse: bool = False
    inpaint_regions: list[tuple[int, int]] = field(default_factory=list)
    speed: float = 1.0
    pitch_semitones: float = 0.0
    filter_type: str | None = None
    filter_cutoff_hz: float | None = None
    reverb_amount: float = 0.0
    grain_density: float = 0.0
    grain_size_ms: float = 120.0
    grain_jitter: float = 0.0
    effects_applied: list[str] = field(default_factory=list)

    # agentic listening
    agent_listening: bool = False
    listening_route: ListeningRoute = ListeningRoute.HYBRID
    generation_engine: GenerationEngine = GenerationEngine.AUTO
    engine_provider: str = ""  # which provider was used (elevenlabs, stability, local, etc.)

    # derivation / lineage
    generation_prompt: str | None = None
    parent_layer_id: str | None = None
    generation_depth: int = 0

    # legacy compat
    is_generated: bool = False

    def __post_init__(self):
        # per-layer lock for atomic buffer+metadata swaps (§1.9)
        # not serialized — purely runtime
        object.__setattr__(self, "_buf_lock", threading.Lock())

    @property
    def is_empty(self) -> bool:
        return self.buffer.shape[0] == 0

    @property
    def length_samples(self) -> int:
        return self.buffer.shape[0]

    def compute_waveform(self, points: int = 64) -> list[float]:
        """compute a summary waveform for display."""
        if self.is_empty:
            self.waveform_data = [0.0] * points
            return self.waveform_data

        mono = np.mean(self.buffer, axis=1) if self.buffer.ndim > 1 else self.buffer
        chunk_size = max(1, len(mono) // points)
        waveform = []
        for i in range(points):
            start = i * chunk_size
            end = min(start + chunk_size, len(mono))
            if start < len(mono):
                rms = float(np.sqrt(np.mean(mono[start:end] ** 2)))
                waveform.append(round(rms, 4))
            else:
                waveform.append(0.0)
        self.waveform_data = waveform
        return waveform


# --- backward compatibility alias ---
LoopLayer = Layer


@dataclass
class CommandLogEntry:
    """a single logged command with its result."""

    timestamp: datetime
    raw_text: str | None
    action_json: dict
    status: str  # "ok" | "error" | "rejected"
    message: str = ""


@dataclass
class LineageNode:
    """a node in the sonic genealogy."""

    id: str
    type: str  # recorded/generated/imported/resampled
    parent: str | None
    route: str | None
    engine: str | None
    prompt: str | None
    depth: int
    timestamp: str = ""


@dataclass
class OramSession:
    """the full state of an oram performance session."""

    id: str
    scene: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sample_rate: int = 48000
    bpm: float | None = None
    layers: list[Layer] = field(default_factory=list)
    commands: list[CommandLogEntry] = field(default_factory=list)
    mode: Mode = Mode.RECORD  # v2: default to recorder
    selected_layer: int = 0
    listening: bool = False
    auto_listen: bool = False
    input_mode: str = "prompt"  # "prompt" or "audio"

    # v1 compat
    generated_bed_id: int | None = None

    def __post_init__(self):
        if not self.layers:
            self.layers = [
                Layer(
                    id=f"layer-{i + 1:03d}",
                    name=f"layer_{i + 1}",
                    slot=i,
                )
                for i in range(4)
            ]

    def get_lineage(self) -> list[LineageNode]:
        """build the sonic genealogy from all layers."""
        nodes = []
        for layer in self.layers:
            nodes.append(LineageNode(
                id=layer.id,
                type=layer.source_type.value,
                parent=layer.parent_layer_id,
                route=layer.listening_route.value if layer.parent_layer_id else None,
                engine=layer.generation_engine.value if layer.parent_layer_id else None,
                prompt=layer.generation_prompt,
                depth=layer.generation_depth,
            ))
        return nodes
