"""oram.stt.base — STT adapter protocol."""

from __future__ import annotations

from typing import Protocol

import numpy as np


class STTAdapter(Protocol):
    """protocol for speech-to-text adapters."""

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """transcribe audio to text."""
        ...
