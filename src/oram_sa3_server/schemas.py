from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ProviderId = Literal["mock", "stable_audio_python", "stable_audio_mlx", "stability_api"]
ModeId = Literal["text-to-audio", "audio-to-audio", "inpainting", "continuation"]
SnapDivision = Literal["1/4", "1/8", "1/16", "1/32", "triplet"]
TimeModuleType = Literal[
    "colony_sequencer",
    "trigger_pads",
    "slicer",
    "melody_maker",
    "euclidean_colony",
    "clocked_looper",
    "probability_gate",
    "clock_divider",
    "humanizer",
    "polymeter",
    "render_bus",
    "render_macros",
]
ControlPortKind = Literal["audio", "control", "event", "prompt", "metadata", "midi", "osc", "cv"]
ControlPortDirection = Literal["input", "output"]
ControlPortScope = Literal[
    "internal",
    "generation",
    "time",
    "library",
    "metadata",
    "hardware",
    "network",
    "export",
]
ControlLineageRole = Literal[
    "none",
    "audio-parent",
    "control-parent",
    "prompt-parent",
    "metadata-parent",
    "midi-parent",
    "osc-parent",
    "cv-parent",
    "hardware-return",
]
ControlCurve = Literal["linear", "exponential", "log", "s_curve", "stepped"]
ControlPolarity = Literal["normal", "inverted"]
ControlAnalysisFeature = Literal[
    "envelope",
    "rms",
    "transient",
    "spectral_centroid",
    "pitch",
    "chroma",
    "onset_density",
    "tempo",
    "timbre",
]
ControlCVMode = Literal["cv", "gate", "clock", "pitch"]
ControlCVRange = Literal["unipolar", "bipolar"]


class LoraSpec(BaseModel):
    path: str
    id: str | None = None
    name: str | None = None
    strength: float | None = Field(default=None, ge=0.0, le=10.0)
    tags: list[str] = Field(default_factory=list)
    author: str | None = None
    license: str | None = None
    source_dataset: str | None = None
    prompt_vocabulary: list[str] = Field(default_factory=list)
    recommended_modules: list[str] = Field(default_factory=list)
    provenance_notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrainCard(BaseModel):
    id: str | None = None
    name: str
    path: str | None = None
    description: str | None = None
    source_dataset: str | None = None
    license: str | None = None
    author: str | None = None
    training_settings: dict[str, Any] = Field(default_factory=dict)
    prompt_vocabulary: list[str] = Field(default_factory=list)
    recommended_modules: list[str] = Field(default_factory=list)
    example_sounds: list[str] = Field(default_factory=list)
    provenance_notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    strength_min: float = Field(default=0.0, ge=0.0, le=10.0)
    strength_max: float = Field(default=1.5, ge=0.0, le=10.0)
    default_strength: float = Field(default=0.7, ge=0.0, le=10.0)
    enabled: bool = True
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("strain name is required")
        return cleaned

    @model_validator(mode="after")
    def validate_strength_range(self) -> "StrainCard":
        if self.strength_max < self.strength_min:
            raise ValueError("strength_max must be greater than or equal to strength_min")
        if not self.strength_min <= self.default_strength <= self.strength_max:
            raise ValueError("default_strength must be inside the strength range")
        return self


class StrainRegistryResponse(BaseModel):
    strains: list[StrainCard] = Field(default_factory=list)


class StrainLoadRequest(BaseModel):
    provider: ProviderId = "stable_audio_python"
    strain_ids: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)


class MicroMatterRequest(BaseModel):
    input_audio_path: str
    metadata_path: str | None = None
    source_id: str | None = None
    module: str = "microscope"
    window_ms: float = Field(default=20.0, ge=5.0, le=1000.0)
    hop_ms: float = Field(default=10.0, ge=5.0, le=1000.0)
    output_name: str | None = None
    lineage: dict[str, Any] = Field(default_factory=dict)


class MicroMatterProfileResult(BaseModel):
    id: str
    status: Literal["done", "error"]
    input_audio_path: str
    profile_file: str | None = None
    metadata_file: str | None = None
    sample_rate: int | None = None
    duration: float | None = None
    module: str = "microscope"
    descriptors: dict[str, Any] = Field(default_factory=dict)
    module_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class BaseGenerationRequest(BaseModel):
    provider: ProviderId = "mock"
    model: str = "mock-sine"
    prompt: str = ""
    negative_prompt: str = ""
    base_prompt: str | None = None
    modulated_prompt: str | None = None
    base_negative_prompt: str | None = None
    modulated_negative_prompt: str | None = None
    modulators: list[dict[str, Any]] = Field(default_factory=list)
    semantic_layers: list[dict[str, Any]] = Field(default_factory=list)
    semantic_effects: list[dict[str, Any]] = Field(default_factory=list)
    generation_context: dict[str, Any] = Field(default_factory=dict)
    prompt_weight: float | None = None
    negative_prompt_weight: float | None = None
    seed_drift: float | None = None
    batch_spread: float | None = None
    inpaint_density: float | None = None
    mask_feather: float | None = None
    continuation_divergence: float | None = None
    brightness_language: float | None = None
    lora_strength: float | None = None
    region_roles: list[dict[str, Any]] = Field(default_factory=list)
    preserve_ranges: list[tuple[float, float]] = Field(default_factory=list)
    accent_ranges: list[tuple[float, float]] = Field(default_factory=list)
    forbidden_ranges: list[tuple[float, float]] = Field(default_factory=list)
    seed_ranges: list[tuple[float, float]] = Field(default_factory=list)
    texture_ranges: list[tuple[float, float]] = Field(default_factory=list)
    variation_ranges: list[tuple[float, float]] = Field(default_factory=list)
    bridge_ranges: list[tuple[float, float]] = Field(default_factory=list)
    silence_ranges: list[tuple[float, float]] = Field(default_factory=list)
    genetic_identities: list[dict[str, Any]] = Field(default_factory=list)
    generation_sequences: list[dict[str, Any]] = Field(default_factory=list)
    duration: float = Field(default=4.0, gt=0.0, le=380.0)
    steps: int = Field(default=8, ge=1, le=250)
    cfg_scale: float = Field(default=1.0, ge=0.0, le=20.0)
    seed: int = -1
    batch_size: int = Field(default=1, ge=1, le=16)
    lora: list[LoraSpec] = Field(default_factory=list)
    output_name: str | None = None
    culture_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    ratings: dict[str, Any] = Field(default_factory=dict)
    waveform_preview: str | None = None
    control_routes: list[dict[str, Any]] = Field(default_factory=list)
    control_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    control_sources: list[dict[str, Any]] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    latents: dict[str, Any] = Field(default_factory=dict)
    latent_file: str | None = None
    latent_fingerprint: str | None = None
    chunked_decode: bool = True
    lineage: dict[str, Any] = Field(default_factory=dict)
    job_id: str | None = Field(default=None, exclude=True)


class GenerateRequest(BaseGenerationRequest):
    pass


class AudioToAudioRequest(BaseGenerationRequest):
    input_audio_path: str
    init_noise_level: float = Field(default=0.45, ge=0.0, le=1.0)


class InpaintRequest(BaseGenerationRequest):
    input_audio_path: str
    inpaint_ranges: list[tuple[float, float]] = Field(default_factory=list)

    @field_validator("inpaint_ranges")
    @classmethod
    def validate_ranges(cls, ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not ranges:
            raise ValueError("at least one inpaint range is required")
        for start, end in ranges:
            if start < 0 or end < 0:
                raise ValueError("inpaint ranges must be non-negative")
            if end <= start:
                raise ValueError("each inpaint range end must be greater than start")
        return ranges


class ContinueRequest(BaseGenerationRequest):
    input_audio_path: str
    source_duration: float = Field(gt=0.0)
    target_duration: float = Field(gt=0.0, le=380.0)

    @field_validator("target_duration")
    @classmethod
    def validate_target_duration(cls, target_duration: float, info: Any) -> float:
        source_duration = info.data.get("source_duration") if hasattr(info, "data") else None
        if source_duration is not None and target_duration <= source_duration:
            raise ValueError("target_duration must be greater than source_duration")
        return target_duration


class LoadModelRequest(BaseModel):
    provider: ProviderId
    model: str
    device: str = "auto"


class LoadModelResponse(BaseModel):
    provider: str
    model: str
    device: str
    status: str
    detail: str | None = None


class LoraLoadRequest(BaseModel):
    provider: ProviderId = "stable_audio_python"
    paths: list[str]


class LoraStrengthRequest(BaseModel):
    provider: ProviderId = "stable_audio_python"
    strength: float = Field(ge=0.0, le=10.0)
    lora_index: int | None = Field(default=None, ge=0)


class ProviderStatus(BaseModel):
    id: str
    available: bool
    models: list[str]
    loaded_model: str | None = None
    device: str = "unknown"
    detail: str | None = None


class ModelsResponse(BaseModel):
    providers: list[ProviderStatus]


class HealthResponse(BaseModel):
    status: str
    server: str
    active_provider: str
    device: str
    models_loaded: list[str]
    output_dir: str


class GenerationResult(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "error"]
    audio_files: list[str] = Field(default_factory=list)
    metadata_files: list[str] = Field(default_factory=list)
    seed: int | None = None
    duration: float | None = None
    sample_rate: int | None = None
    error: str | None = None
    provider: str | None = None
    model: str | None = None
    mode: str | None = None


class TimeClock(BaseModel):
    enabled: bool = True
    bpm: float = Field(default=120.0, ge=20.0, le=300.0)
    beats_per_bar: int = Field(default=4, ge=1, le=16)
    beat_unit: int = Field(default=4, ge=1, le=32)
    bars: int = Field(default=4, ge=1, le=128)
    ppq: int = Field(default=960, ge=24, le=3840)
    # Stable Audio 3 SAME latents are fixed at 44.1 kHz stereo, so time render
    # locks the clock sample rate to 44100 to match the encoder/decoder.
    sample_rate: Literal[44100] = 44100
    snap_division: SnapDivision = "1/16"
    swing: float = Field(default=0.0, ge=0.0, le=1.0)
    loop_start_tick: int = Field(default=0, ge=0)
    loop_end_tick: int | None = Field(default=None, ge=1)

    @field_validator("beat_unit")
    @classmethod
    def validate_beat_unit(cls, beat_unit: int) -> int:
        if beat_unit not in {1, 2, 4, 8, 16, 32}:
            raise ValueError("beat_unit must be a common note denominator")
        return beat_unit

    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    def total_beats(self) -> float:
        return float(self.bars * self.beats_per_bar)

    def loop_seconds(self) -> float:
        return self.total_beats() * self.seconds_per_beat()

    def loop_samples(self) -> int:
        return round(self.loop_seconds() * self.sample_rate)

    def ticks_per_bar(self) -> int:
        return self.beats_per_bar * self.ppq

    def total_ticks(self) -> int:
        return self.bars * self.ticks_per_bar()

    def resolved_loop_end_tick(self) -> int:
        return self.loop_end_tick or self.total_ticks()


class TimeRenderSource(BaseModel):
    id: str
    audio_path: str
    metadata_path: str | None = None
    label: str | None = None
    gain: float = Field(default=1.0, ge=0.0, le=2.0)
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)


class TimeRenderEvent(BaseModel):
    tick: int = Field(ge=0)
    source_id: str
    lane: int | None = Field(default=None, ge=0)
    pad: int | None = Field(default=None, ge=0)
    velocity: float = Field(default=1.0, ge=0.0, le=2.0)
    gain: float = Field(default=1.0, ge=0.0, le=2.0)
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    pitch_semitones: float = Field(default=0.0, ge=-48.0, le=48.0)
    source_start_sec: float | None = Field(default=None, ge=0.0)
    source_end_sec: float | None = Field(default=None, gt=0.0)
    fade_in_ms: float = Field(default=0.0, ge=0.0, le=5000.0)
    fade_out_ms: float = Field(default=5.0, ge=0.0, le=5000.0)
    variation: int | None = Field(default=None, ge=0)
    duration_ticks: int | None = Field(default=None, ge=1)
    reverse: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_window(self) -> "TimeRenderEvent":
        if self.source_start_sec is not None and self.source_end_sec is not None:
            if self.source_end_sec <= self.source_start_sec:
                raise ValueError("source_end_sec must be greater than source_start_sec")
        return self


class TimeRenderRequest(BaseModel):
    module_type: TimeModuleType
    module_id: str
    clock: TimeClock = Field(default_factory=TimeClock)
    sources: list[TimeRenderSource] = Field(min_length=1, max_length=64)
    events: list[TimeRenderEvent] = Field(min_length=1, max_length=4096)
    output_name: str | None = None
    culture_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    prompt: str = ""
    negative_prompt: str = ""
    duration: float | None = Field(default=None, gt=0.0, le=380.0)
    seed: int = -1
    normalize: bool = True
    lora: list[LoraSpec] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    latents: dict[str, Any] = Field(default_factory=dict)
    waveform_preview: str | None = None
    lineage: dict[str, Any] = Field(default_factory=dict)
    modulators: list[dict[str, Any]] = Field(default_factory=list)
    control_routes: list[dict[str, Any]] = Field(default_factory=list)
    control_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    control_sources: list[dict[str, Any]] = Field(default_factory=list)
    job_id: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_unique_source_ids(self) -> "TimeRenderRequest":
        ids = [source.id for source in self.sources]
        if len(set(ids)) != len(ids):
            raise ValueError("time render sources must have unique ids")
        return self


class JobStatus(BaseModel):
    job_id: str
    status: str
    mode: str
    provider: str | None = None
    model: str | None = None
    request: dict[str, Any] = Field(default_factory=dict)
    audio_files: list[str] = Field(default_factory=list)
    metadata_files: list[str] = Field(default_factory=list)
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class JobSubmitRequest(BaseModel):
    mode: ModeId
    request: dict[str, Any]


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str
    mode: ModeId
    provider: str | None = None
    model: str | None = None
    status_url: str
    events_url: str


class ControlTransform(BaseModel):
    amount: float = Field(default=1.0, ge=0.0, le=4.0)
    polarity: ControlPolarity = "normal"
    curve: ControlCurve = "linear"
    min: float | None = None
    max: float | None = None
    smoothing_ms: float = Field(default=0.0, ge=0.0, le=60000.0)
    slew_ms: float = Field(default=0.0, ge=0.0, le=60000.0)
    quantize_steps: int | None = Field(default=None, ge=2, le=4096)
    probability: float = Field(default=1.0, ge=0.0, le=1.0)
    clock_sync: bool = False
    division: SnapDivision | None = None
    clamp_min: float | None = None
    clamp_max: float | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "ControlTransform":
        if self.min is not None and self.max is not None and self.max < self.min:
            raise ValueError("max must be greater than or equal to min")
        if self.clamp_min is not None and self.clamp_max is not None and self.clamp_max < self.clamp_min:
            raise ValueError("clamp_max must be greater than or equal to clamp_min")
        return self


class ControlPort(BaseModel):
    id: str
    label: str
    kind: ControlPortKind
    direction: ControlPortDirection
    scope: ControlPortScope = "internal"
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlRoute(BaseModel):
    id: str | None = None
    label: str | None = None
    source_port_id: str
    target_port_id: str
    source_kind: ControlPortKind
    target_kind: ControlPortKind
    enabled: bool = True
    transform: ControlTransform = Field(default_factory=ControlTransform)
    lineage_role: ControlLineageRole = "control-parent"
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_port_id", "target_port_id")
    @classmethod
    def validate_port_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("port id cannot be empty")
        return value


class ControlEvent(BaseModel):
    id: str | None = None
    route_id: str | None = None
    port_id: str | None = None
    kind: ControlPortKind = "event"
    source: str = "dashboard"
    value: Any = None
    timestamp: str | None = None
    tick: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlSnapshot(BaseModel):
    id: str
    captured_at: str
    routes: list[ControlRoute] = Field(default_factory=list)
    events: list[ControlEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlPortsResponse(BaseModel):
    ports: list[ControlPort]


class ControlRoutesResponse(BaseModel):
    routes: list[ControlRoute]


class ControlEventsResponse(BaseModel):
    events: list[ControlEvent]


class ControlRouteEnableRequest(BaseModel):
    enabled: bool


class ControlAudioAnalysisRequest(BaseModel):
    input_audio_path: str
    metadata_path: str | None = None
    source_id: str | None = None
    features: list[ControlAnalysisFeature] = Field(
        default_factory=lambda: ["envelope", "rms", "transient", "spectral_centroid"]
    )
    window_ms: float = Field(default=40.0, ge=5.0, le=1000.0)
    hop_ms: float = Field(default=20.0, ge=5.0, le=1000.0)
    smooth: float = Field(default=0.15, ge=0.0, le=1.0)
    normalize: bool = True
    output_name: str | None = None
    lineage: dict[str, Any] = Field(default_factory=dict)

    @field_validator("features")
    @classmethod
    def validate_features(cls, value: list[ControlAnalysisFeature]) -> list[ControlAnalysisFeature]:
        if not value:
            raise ValueError("at least one feature is required")
        unique = list(dict.fromkeys(value))
        return unique


class ControlFeatureSummary(BaseModel):
    feature: ControlAnalysisFeature
    point_count: int
    min: float
    max: float
    mean: float
    peak_time_sec: float | None = None
    event_count: int = 0


class ControlAnalysisResult(BaseModel):
    id: str
    status: Literal["done", "error"]
    input_audio_path: str
    control_files: list[str] = Field(default_factory=list)
    metadata_file: str | None = None
    sample_rate: int | None = None
    duration: float | None = None
    features: list[ControlFeatureSummary] = Field(default_factory=list)
    route_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class ControlPoint(BaseModel):
    t: float = Field(ge=0.0)
    value: float = Field(ge=-1.0, le=1.0)


class ControlCVRenderRequest(BaseModel):
    input_control_path: str | None = None
    feature: ControlAnalysisFeature | None = None
    points: list[ControlPoint] = Field(default_factory=list)
    duration: float = Field(default=4.0, gt=0.0, le=380.0)
    sample_rate: Literal[44100] = 44100
    output_name: str | None = None
    mode: ControlCVMode = "cv"
    range: ControlCVRange = "unipolar"
    scale: float = Field(default=1.0, ge=0.0, le=1.0)
    offset: float = Field(default=0.0, ge=-1.0, le=1.0)
    clamp_min: float = Field(default=-1.0, ge=-1.0, le=1.0)
    clamp_max: float = Field(default=1.0, ge=-1.0, le=1.0)
    slew_ms: float = Field(default=0.0, ge=0.0, le=60000.0)
    gate_value: float = Field(default=1.0, ge=0.0, le=1.0)
    lineage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> "ControlCVRenderRequest":
        if not self.input_control_path and not self.points:
            raise ValueError("provide input_control_path or points")
        if self.clamp_max < self.clamp_min:
            raise ValueError("clamp_max must be greater than or equal to clamp_min")
        return self


class ControlCVRenderResult(BaseModel):
    status: Literal["done", "error"]
    audio_file: str | None = None
    metadata_file: str | None = None
    duration: float | None = None
    sample_rate: int | None = None
    mode: ControlCVMode | None = None
    cv_safe: bool = True
    hardware_output: bool = False
    error: str | None = None


class ControlBridgeStatus(BaseModel):
    osc_udp_send: bool = True
    osc_udp_receive: bool = False
    midi_browser: bool = True
    midi_native: bool = False
    cv_hardware_output: bool = False
    cv_profiles: int = 0
    armed_cv_outputs: int = 0
    detail: dict[str, Any] = Field(default_factory=dict)


class ControlOSCMessage(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=9000, ge=1, le=65535)
    address: str = "/germ/value"
    values: list[float | int | str] = Field(default_factory=list)
    rate_limit_hz: float = Field(default=60.0, gt=0.0, le=1000.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("address")
    @classmethod
    def validate_osc_address(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/") or " " in value:
            raise ValueError("OSC address must start with / and contain no spaces")
        return value

    @model_validator(mode="after")
    def validate_values(self) -> "ControlOSCMessage":
        if not self.values:
            self.values = [0.0]
        if len(self.values) > 32:
            raise ValueError("OSC messages support up to 32 values")
        return self


class ControlOSCResult(BaseModel):
    status: Literal["sent", "recorded", "error"]
    host: str
    port: int
    address: str
    byte_count: int = 0
    sent: bool = False
    error: str | None = None


class ControlMIDIMessage(BaseModel):
    backend: Literal["browser", "native_optional", "event"] = "event"
    device: str | None = None
    channel: int = Field(default=1, ge=1, le=16)
    type: Literal["note_on", "note_off", "cc", "clock", "transport"] = "cc"
    note: int | None = Field(default=None, ge=0, le=127)
    cc: int | None = Field(default=None, ge=0, le=127)
    value: int = Field(default=64, ge=0, le=127)
    velocity: int = Field(default=96, ge=0, le=127)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlMIDIResult(BaseModel):
    status: Literal["sent", "recorded", "unsupported", "error"]
    sent: bool = False
    backend: str
    detail: str | None = None


class ControlCVProfile(BaseModel):
    id: str | None = None
    name: str
    interface_label: str | None = None
    output_channel: int = Field(ge=1, le=256)
    mode: ControlCVMode = "cv"
    range: ControlCVRange = "unipolar"
    volts_per_unit: float = Field(default=1.0, gt=0.0, le=10.0)
    offset_volts: float = Field(default=0.0, ge=-10.0, le=10.0)
    clamp_min_volts: float = Field(default=0.0, ge=-10.0, le=10.0)
    clamp_max_volts: float = Field(default=5.0, ge=-10.0, le=10.0)
    slew_limit_ms: float = Field(default=5.0, ge=0.0, le=60000.0)
    gate_voltage: float = Field(default=5.0, ge=0.0, le=10.0)
    pulse_width_ms: float = Field(default=10.0, ge=1.0, le=10000.0)
    speaker_protection: bool = True
    calibrated: bool = False
    armed: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_cv_profile(self) -> "ControlCVProfile":
        if self.clamp_max_volts < self.clamp_min_volts:
            raise ValueError("clamp_max_volts must be greater than or equal to clamp_min_volts")
        if self.armed and (not self.calibrated or not self.speaker_protection):
            raise ValueError("CV profile must be calibrated and speaker-protected before arming")
        return self


class ControlCVProfilesResponse(BaseModel):
    profiles: list[ControlCVProfile]


class ControlCVArmRequest(BaseModel):
    armed: bool
    confirm: bool = False


class ControlGeneticGraphResponse(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
