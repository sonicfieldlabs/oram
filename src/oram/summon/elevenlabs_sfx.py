"""oram.summon.elevenlabs_sfx — ElevenLabs SFX generator stub.

optional API generator controlled by ELEVENLABS_API_KEY env var.
"""

from __future__ import annotations

import numpy as np

from oram_security.credentials import resolve_provider_secret


class ElevenLabsSFXGenerator:
    """generates sound effects using the ElevenLabs API.

    requires ELEVENLABS_API_KEY environment variable.
    """

    def __init__(self):
        self._api_key = resolve_provider_secret("elevenlabs")
        if not self._api_key:
            raise ValueError(
                "ElevenLabs credential is not configured. "
                "run `oram credentials set elevenlabs` or use --generator-backend=mock"
            )

    def generate(self, prompt: str, duration: float, sample_rate: int) -> np.ndarray:
        """generate audio from a text prompt via ElevenLabs API."""
        raise NotImplementedError("ElevenLabs SFX generator not yet implemented")
