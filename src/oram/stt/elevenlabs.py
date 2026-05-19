"""oram.stt.elevenlabs — ElevenLabs STT adapter stub."""

from __future__ import annotations

import numpy as np


class ElevenLabsSTTAdapter:
    """ElevenLabs speech-to-text adapter — not yet implemented.

    would use the ElevenLabs API for cloud-based STT.
    requires ELEVENLABS_API_KEY environment variable.
    """

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        raise NotImplementedError("ElevenLabs STT adapter not yet implemented")
