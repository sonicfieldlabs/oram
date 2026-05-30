"""oram.command.schemas — pydantic action models for all command types.

oram v2: adds listening, derivation, layer mode, and engine actions.

all commands, whether keyboard, parsed text, or LLM-assisted, must become a
structured action before touching the engine.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from oram.constants import MAX_LAYERS

# valid effect names
VALID_EFFECTS = frozenset({
    "reverse",
    "speed",
    "pitch",
    "lowpass",
    "highpass",
    "reverb",
    "granular",
    "fade_in",
    "fade_out",
    "trim_start",
    "trim_end",
    "spatial_far",
    "stretch_breathe",
})

VALID_LISTENING_ROUTES = frozenset({
    "technical", "descriptive", "speculative", "hybrid",
})

VALID_ENGINES = frozenset({
    # intent-based (backward compat)
    "auto", "sfx", "voice", "music",
    # provider-specific engine IDs
    "elevenlabs-sfx", "elevenlabs-tts", "elevenlabs-music", "elevenlabs-scribe",
    "elevenlabs-voice-changer", "elevenlabs-voice-design", "elevenlabs-isolation",
    "stability-stable-audio-2", "stable-audio-2", "stable-audio-25",
    "stability-stable-audio-25", "stability-stable-audio-3", "stable-audio-3",
    "stable-audio-3-local", "local", "local-mock",
})

VALID_ENGINE_PREFIXES = ("elevenlabs-", "stability-", "stable-audio-", "local-")


def _is_valid_engine(value: str) -> bool:
    return value in VALID_ENGINES or any(value.startswith(prefix) for prefix in VALID_ENGINE_PREFIXES)

VALID_LAYER_MODES = frozenset({
    "recorder", "looper", "sampler",
})

VALID_DECAYS = frozenset({"short", "medium", "long"})

# --- transport actions ---


class RecordAction(BaseModel):
    """start recording into the selected or specified layer."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["record"] = "record"
    target: int | str = "selected"
    duration: float | None = None
    bars: int | None = None
    overdub: bool = False

    @field_validator("duration")
    @classmethod
    def _clamp_duration(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("duration must be positive")
        return v


class StopRecordingAction(BaseModel):
    """stop the current recording."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["stop_recording"] = "stop_recording"


class KillAudioAction(BaseModel):
    """immediately silence all ORAM audio activity."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["kill_audio"] = "kill_audio"


class OverdubAction(BaseModel):
    """overdub into the selected or specified layer."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["overdub"] = "overdub"
    target: int | str = "selected"
    duration: float | None = None


class SelectLayerAction(BaseModel):
    """select a layer by number."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["select_layer"] = "select_layer"
    target: int = Field(ge=1, le=MAX_LAYERS)


class MuteLayerAction(BaseModel):
    """toggle mute on a layer."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["mute_layer"] = "mute_layer"
    target: int | str = "selected"


class SoloLayerAction(BaseModel):
    """toggle solo on a layer."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["solo_layer"] = "solo_layer"
    target: int | str = "selected"


class ClearLayerAction(BaseModel):
    """clear a layer's buffer."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["clear_layer"] = "clear_layer"
    target: int | str = "selected"
    confirmed: bool = False


# --- mix actions ---


class SetVolumeAction(BaseModel):
    """set volume on a layer or master."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["set_volume"] = "set_volume"
    target: int | str = "selected"
    volume: float = Field(ge=0.0, le=2.0)


class SetPanAction(BaseModel):
    """set pan on a layer."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["set_pan"] = "set_pan"
    target: int | str = "selected"
    pan: float = Field(ge=-1.0, le=1.0)


# --- effect actions ---


class EffectParameters(BaseModel):
    """parameters for an effect application."""

    model_config = ConfigDict(extra="forbid")

    # speed
    speed: float | None = Field(None, ge=0.25, le=4.0)
    # pitch
    semitones: float | None = Field(None, ge=-12.0, le=12.0)
    # filter
    cutoff_hz: float | None = Field(None, ge=20.0, le=20000.0)
    # reverb
    wet: float | None = Field(None, ge=0.0, le=1.0)
    decay: str | None = None  # "short", "medium", "long"
    # granular
    density: float | None = Field(None, ge=0.0, le=1.0)
    grain_size_ms: float | None = Field(None, ge=10.0, le=500.0)
    jitter: float | None = Field(None, ge=0.0, le=1.0)
    texture: str | None = None
    # fade
    fade_seconds: float | None = Field(None, ge=0.0)
    # spatial
    narrow: bool | None = None

    @field_validator("decay")
    @classmethod
    def _valid_decay(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_DECAYS:
            raise ValueError(f"decay must be one of {VALID_DECAYS}")
        return v


class ApplyEffectAction(BaseModel):
    """apply a DSP effect to a layer."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["apply_effect"] = "apply_effect"
    target: int | str = "selected"
    effect: str  # see VALID_EFFECTS in this module
    parameters: EffectParameters = Field(default_factory=EffectParameters)

    @field_validator("effect")
    @classmethod
    def _valid_effect(cls, effect: str) -> str:
        if effect not in VALID_EFFECTS:
            raise ValueError(f"invalid effect: {effect}")
        return effect


class RemoveEffectAction(BaseModel):
    """remove an effect from a layer."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["remove_effect"] = "remove_effect"
    target: int | str = "selected"
    effect: str

    @field_validator("effect")
    @classmethod
    def _valid_effect(cls, effect: str) -> str:
        if effect not in VALID_EFFECTS:
            raise ValueError(f"invalid effect: {effect}")
        return effect


# --- generative actions ---


class GenerateLayerAction(BaseModel):
    """generate a sound layer via any registered engine."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["generate_layer"] = "generate_layer"
    target: str = "generated_bed"
    prompt: str
    duration: float = Field(default=16.0, ge=0.5, le=600.0)
    loop: bool = True
    mix_level: float = Field(default=0.3, ge=0.0, le=1.0)
    engine: str = "auto"  # auto / sfx / voice / music / provider-specific IDs
    provider: str = ""     # explicit provider override: elevenlabs / stability / local / etc.
    intent: str = "auto"   # sonic intent: voice / sound_effect / music / texture / transform

    @field_validator("engine")
    @classmethod
    def _valid_engine(cls, v: str) -> str:
        if not _is_valid_engine(v):
            raise ValueError(f"invalid engine: {v}")
        return v


# --- v2: listening actions ---


class ListenAction(BaseModel):
    """listen to a layer through a configured route."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["listen"] = "listen"
    target: int | str = "selected"
    route: str = "hybrid"  # technical / descriptive / speculative / hybrid

    @field_validator("route")
    @classmethod
    def _valid_route(cls, route: str) -> str:
        if route not in VALID_LISTENING_ROUTES:
            raise ValueError(f"invalid route: {route}")
        return route


class GenerateFromAction(BaseModel):
    """listen to a layer, then generate a new layer from that listening."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["generate_from"] = "generate_from"
    target: int | str = "selected"
    route: str = "hybrid"
    engine: str = "auto"
    duration: float | None = None
    provider: str = ""     # explicit provider override
    intent: str = "auto"   # sonic intent override

    @field_validator("route")
    @classmethod
    def _valid_route(cls, route: str) -> str:
        if route not in VALID_LISTENING_ROUTES:
            raise ValueError(f"invalid route: {route}")
        return route

    @field_validator("engine")
    @classmethod
    def _valid_engine(cls, v: str) -> str:
        if not _is_valid_engine(v):
            raise ValueError(f"invalid engine: {v}")
        return v


class ReplaceLayerAction(BaseModel):
    """replace a layer's audio with another layer's audio."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["replace_layer"] = "replace_layer"
    source: int | str  # layer to take audio from
    target: int | str  # layer to replace


class ForkLayerAction(BaseModel):
    """clone a layer into an empty slot."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["fork_layer"] = "fork_layer"
    target: int | str = "selected"


class ListenAgainAction(BaseModel):
    """re-listen to a generated layer (recursive listening)."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["listen_again"] = "listen_again"
    target: int | str = "selected"
    route: str = "hybrid"
    engine: str = "auto"

    @field_validator("route")
    @classmethod
    def _valid_route(cls, route: str) -> str:
        if route not in VALID_LISTENING_ROUTES:
            raise ValueError(f"invalid route: {route}")
        return route

    @field_validator("engine")
    @classmethod
    def _valid_engine(cls, v: str) -> str:
        if not _is_valid_engine(v):
            raise ValueError(f"invalid engine: {v}")
        return v


# --- v2: layer mode actions ---


class SetLayerModeAction(BaseModel):
    """set a layer's behavior mode."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["set_layer_mode"] = "set_layer_mode"
    target: int | str = "selected"
    mode: str  # recorder / looper / sampler

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, mode: str) -> str:
        if mode not in VALID_LAYER_MODES:
            raise ValueError(f"invalid layer mode: {mode}")
        return mode


# --- analysis actions ---


class AnalyzeMixAction(BaseModel):
    """analyze the current mix and generate a listening report."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["analyze_mix"] = "analyze_mix"
    target: int | str | None = None
    focus: str | None = None  # "density", "speech", "fatigue", etc.


# --- session actions ---


class SaveSessionAction(BaseModel):
    """save the current session."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["save_session"] = "save_session"


class ExportMixAction(BaseModel):
    """export the mix and stems as WAV files."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["export_mix"] = "export_mix"


class SetModeAction(BaseModel):
    """change the oram operating mode."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["set_mode"] = "set_mode"
    mode: str  # listen, record, loop, shape, summon, sleep


class QuitAction(BaseModel):
    """quit oram gracefully."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["quit"] = "quit"


# --- loop region ---


class SetLoopRegionAction(BaseModel):
    """set the loop region on a layer."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["set_loop_region"] = "set_loop_region"
    target: int | str = "selected"
    start_pct: float | None = Field(None, ge=0.0, le=100.0)
    end_pct: float | None = Field(None, ge=0.0, le=100.0)
    start_seconds: float | None = Field(None, ge=0.0)
    end_seconds: float | None = Field(None, ge=0.0)
    enabled: bool = True


# --- unknown / rejected ---


class UnknownAction(BaseModel):
    """unrecognized or rejected command."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["unknown"] = "unknown"
    reason: str = "unrecognized command"
    raw_text: str | None = None


# discriminated union of all action types
OramAction = Annotated[
    RecordAction
    | StopRecordingAction
    | KillAudioAction
    | OverdubAction
    | SelectLayerAction
    | MuteLayerAction
    | SoloLayerAction
    | ClearLayerAction
    | SetVolumeAction
    | SetPanAction
    | ApplyEffectAction
    | RemoveEffectAction
    | GenerateLayerAction
    | ListenAction
    | GenerateFromAction
    | ReplaceLayerAction
    | ForkLayerAction
    | ListenAgainAction
    | SetLayerModeAction
    | SetLoopRegionAction
    | AnalyzeMixAction
    | SaveSessionAction
    | ExportMixAction
    | SetModeAction
    | QuitAction
    | UnknownAction,
    Field(discriminator="action"),
]
