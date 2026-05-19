"""oram configuration — env vars, CLI args, defaults.

oram v2: adds ElevenLabs gateway config, listening routes, python-dotenv loading.

IMPORTANT: _load_dotenv() is NOT called on import. It must be called
explicitly at CLI startup before OramConfig.from_env().
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv() -> None:
    """load .env file from project root or cwd if present.

    uses python-dotenv for robust parsing (quoted values, comments, export).
    must be called explicitly at CLI startup, not on import.
    """
    from dotenv import load_dotenv as _dotenv_load

    # try project root first (relative to this file)
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        _dotenv_load(env_path, override=False)
        return

    # fallback to cwd
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        _dotenv_load(env_path, override=False)


def _bool_env(key: str, default: bool = False) -> bool:
    """parse a boolean env var."""
    v = os.environ.get(key, "")
    if not v:
        return default
    return v.lower() in ("1", "true", "yes", "on")


@dataclass
class OramConfig:
    """runtime configuration with precedence: cli > env > defaults."""

    sample_rate: int = 48000
    block_size: int = 512
    channels_out: int = 2
    channels_in: int = 1

    input_device: int | str | None = None
    output_device: int | str | None = None

    session_dir: Path = field(default_factory=lambda: Path("./oram_sessions"))
    session_name: str | None = None

    stt_backend: str = "mock"
    generator_backend: str = "mock"  # safe default: no cloud calls
    llm_backend: str = "none"  # safe default: no cloud calls

    mock_audio: bool = False
    no_stt: bool = False

    max_loop_seconds: float = 120.0
    max_generated_seconds: float = 60.0
    default_generated_seconds: float = 16.0

    tui_fps: int = 15

    # v2: ElevenLabs gateway
    elevenlabs_api_key: str = ""
    default_listening_route: str = "hybrid"
    default_engine: str = "auto"
    auto_listen: bool = False  # safe default: off

    # v3: multi-provider engine keys
    stability_api_key: str = ""
    hf_token: str = ""
    fal_key: str = ""
    replicate_api_token: str = ""

    # v3: engine router
    engine_router_mode: str = "auto"  # "auto" | "manual"
    preferred_provider: str = ""      # user's preferred provider override

    # v2: dashboard security
    dashboard_token: str = ""

    @classmethod
    def from_env(cls) -> OramConfig:
        """build config from environment variables, falling back to defaults."""
        cfg = cls()
        if v := os.environ.get("ORAM_SAMPLE_RATE"):
            cfg.sample_rate = int(v)
        if v := os.environ.get("ORAM_BLOCK_SIZE"):
            cfg.block_size = int(v)
        if v := os.environ.get("ORAM_INPUT_DEVICE"):
            cfg.input_device = int(v) if v.isdigit() else v
        if v := os.environ.get("ORAM_OUTPUT_DEVICE"):
            cfg.output_device = int(v) if v.isdigit() else v
        if v := os.environ.get("ORAM_SESSION_DIR"):
            cfg.session_dir = Path(v)
        if v := os.environ.get("ORAM_STT_BACKEND"):
            cfg.stt_backend = v
        if v := os.environ.get("ORAM_GENERATOR_BACKEND"):
            cfg.generator_backend = v
        if v := os.environ.get("ORAM_LLM_BACKEND"):
            cfg.llm_backend = v
        if v := os.environ.get("ELEVENLABS_API_KEY"):
            cfg.elevenlabs_api_key = v
        if v := os.environ.get("ORAM_DEFAULT_LISTENING_ROUTE"):
            cfg.default_listening_route = v
        if v := os.environ.get("ORAM_DEFAULT_ENGINE"):
            cfg.default_engine = v
        if v := os.environ.get("ORAM_DASHBOARD_TOKEN"):
            cfg.dashboard_token = v

        # v3: multi-provider keys
        if v := os.environ.get("STABILITY_API_KEY"):
            cfg.stability_api_key = v
        if v := os.environ.get("HF_TOKEN"):
            cfg.hf_token = v
        if v := os.environ.get("FAL_KEY"):
            cfg.fal_key = v
        if v := os.environ.get("REPLICATE_API_TOKEN"):
            cfg.replicate_api_token = v
        if v := os.environ.get("ORAM_ENGINE_ROUTER_MODE"):
            cfg.engine_router_mode = v
        if v := os.environ.get("ORAM_PREFERRED_PROVIDER"):
            cfg.preferred_provider = v

        # boolean env vars
        cfg.auto_listen = _bool_env("ORAM_AUTO_LISTEN", cfg.auto_listen)

        return cfg

    def validate_duration(self, duration: float, kind: str = "loop") -> float:
        """clamp a duration to configured max."""
        max_dur = self.max_loop_seconds if kind == "loop" else self.max_generated_seconds
        return max(0.0, min(duration, max_dur))
