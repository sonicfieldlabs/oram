"""oram.stt.elevenlabs — ElevenLabs Scribe speech-to-text adapter."""

from __future__ import annotations

import numpy as np

from oram_security.credentials import resolve_provider_secret


class ElevenLabsSTTAdapter:
    """speech-to-text adapter backed by ElevenLabs Scribe."""

    def __init__(self, api_key: str | None = None, params: dict | None = None):
        self._api_key = api_key or resolve_provider_secret("elevenlabs") or ""
        self._params = params or {"tag_audio_events": True, "diarize": False}

    def is_available(self) -> bool:
        return bool(self._api_key)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """transcribe command audio and return plain text."""
        if not self._api_key:
            raise RuntimeError("ELEVENLABS_API_KEY or Keychain credential is required for ElevenLabs STT")
        if audio.shape[0] == 0:
            return ""

        from oram.gateway.scribe import ScribeAdapter

        result = ScribeAdapter(api_key=self._api_key).transcribe(
            audio=audio,
            sample_rate=sample_rate,
            params=self._params,
        )
        return result.text.strip()
