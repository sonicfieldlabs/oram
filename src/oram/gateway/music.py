"""oram.gateway.music — ElevenLabs Music generation adapter.

uses the music generation endpoint for longer beds, harmonic derivations,
ambient layers, and instrumental expansions.
"""

from __future__ import annotations

import io
import os

import numpy as np

from oram.gateway.base import EngineResult
from oram.gateway.client import ElevenLabsHTTPClient

API_BASE = "https://api.elevenlabs.io/v1"


class MusicAdapter:
    """generates music using ElevenLabs Music API."""

    engine_name = "music"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, prompt: str, params: dict | None = None) -> EngineResult:
        """generate music from text prompt or composition plan.

        params:
            duration_seconds: float (3-600, default 30)
            force_instrumental: bool (default True)
            model_id: str (default: music_v1)
        """
        params = params or {}
        duration_ms = int(params.get("duration_seconds", 30.0) * 1000)
        duration_ms = max(3000, min(600000, duration_ms))

        body = {
            "prompt": prompt,
            "music_length_ms": duration_ms,
            "model_id": params.get("model_id", "music_v1"),
            "force_instrumental": params.get("force_instrumental", True),
        }

        client = ElevenLabsHTTPClient(
            self._api_key,
            timeout=120.0,
            output_format=params.get("output_format"),
        )
        response = client.post_json("/music", body)

        audio, sample_rate = self._decode_audio(response.content)

        return EngineResult(
            audio=audio,
            sample_rate=sample_rate,
            engine="music",
            prompt_used=prompt,
            parameters=body,
            duration_seconds=duration_ms / 1000.0,
            cost_credits=client.parse_cost_header(response) or self._estimate_cost(duration_ms / 1000.0),
        )

    def _decode_audio(self, data: bytes) -> tuple[np.ndarray, int]:
        """decode audio bytes to stereo float32 numpy array."""
        try:
            import soundfile as sf
            buf = io.BytesIO(data)
            audio, sr = sf.read(buf)
            if audio.ndim == 1:
                audio = np.column_stack([audio, audio])
            elif audio.ndim == 2 and audio.shape[1] > 2:
                audio = audio[:, :2]
            return audio.astype(np.float32), int(sr)
        except Exception:
            from oram.gateway.base import GenerationFailedError
            raise GenerationFailedError(
                prompt="[music decode]",
                status=0,
                body=f"failed to decode {len(data)} bytes of audio",
            )

    def _estimate_cost(self, duration: float) -> float:
        """rough cost estimate in credits."""
        return round(duration * 50, 1)
