"""oram.stt.whisper_cpp — whisper.cpp adapter stub.

placeholder for future whisper.cpp integration for lower latency.
"""

from __future__ import annotations

import numpy as np


class WhisperCppAdapter:
    """whisper.cpp adapter — not yet implemented.

    would use the whisper.cpp library for lower-latency
    speech-to-text on CPU.
    """

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        raise NotImplementedError("whisper.cpp adapter not yet implemented")
