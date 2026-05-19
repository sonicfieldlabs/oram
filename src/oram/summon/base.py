"""oram.summon.base — sound generator protocol."""

from __future__ import annotations

from typing import Protocol

import numpy as np


class SoundGenerator(Protocol):
    """protocol for generative sound adapters."""

    def generate(self, prompt: str, duration: float, sample_rate: int) -> np.ndarray:
        """generate audio from a text prompt.

        returns a stereo float32 numpy array.
        """
        ...
