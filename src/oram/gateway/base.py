"""oram.gateway.base — engine adapter protocol and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


class GenerationFailedError(Exception):
    """raised when a cloud generation fails to decode audio.

    always preferred over silently returning a zero buffer.
    """

    def __init__(self, prompt: str, status: int = 0, body: str = ""):
        self.prompt = prompt
        self.status = status
        self.body = body[:200]
        super().__init__(
            f"generation failed (status={status}): {body[:200]}"
        )


@dataclass
class EngineResult:
    """result from an engine generation call."""

    audio: np.ndarray  # stereo float32
    sample_rate: int
    engine: str
    prompt_used: str
    parameters: dict = field(default_factory=dict)
    duration_seconds: float = 0.0
    cost_credits: float = 0.0

    def __post_init__(self):
        if self.duration_seconds == 0.0 and self.audio.shape[0] > 0:
            self.duration_seconds = self.audio.shape[0] / self.sample_rate


class EngineAdapter(Protocol):
    """protocol for ElevenLabs engine adapters."""

    engine_name: str

    def generate(self, prompt: str, params: dict) -> EngineResult:
        """generate audio from a prompt."""
        ...

    def is_available(self) -> bool:
        """check if the engine is available."""
        ...
