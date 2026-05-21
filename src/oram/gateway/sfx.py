"""oram.gateway.sfx — ElevenLabs Text-to-Sound-Effects adapter.

uses the /v1/sound-generation endpoint for SFX, foley, textures,
abstract sound objects, environmental fragments, and loopable beds.
"""

from __future__ import annotations

import io
import struct
import wave

import numpy as np

from oram.gateway.base import EngineResult
from oram.gateway.client import ElevenLabsHTTPClient
from oram_security.credentials import resolve_provider_secret

# ElevenLabs API base
API_BASE = "https://api.elevenlabs.io/v1"


class SFXAdapter:
    """generates sound effects using ElevenLabs Text-to-Sound-Effects API."""

    engine_name = "sfx"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or resolve_provider_secret("elevenlabs") or ""

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, prompt: str, params: dict | None = None) -> EngineResult:
        """generate SFX from text prompt.

        params:
            duration_seconds: float (0.5-30, default 4.0)
            prompt_influence: float (0.0-1.0, default 0.3)
        """
        params = params or {}
        duration = params.get("duration_seconds", 4.0)
        duration = max(0.5, min(30.0, duration))
        prompt_influence = params.get("prompt_influence", 0.3)

        body = {
            "text": prompt,
            "duration_seconds": duration,
            "prompt_influence": prompt_influence,
        }

        client = ElevenLabsHTTPClient(
            self._api_key,
            timeout=60.0,
            output_format=params.get("output_format"),
        )
        response = client.post_json("/sound-generation", body)

        # decode audio from response bytes (MP3 by default)
        audio, sample_rate = self._decode_audio(response.content)

        return EngineResult(
            audio=audio,
            sample_rate=sample_rate,
            engine="sfx",
            prompt_used=prompt,
            parameters=body,
            cost_credits=client.parse_cost_header(response) or self._estimate_cost(duration),
        )

    def _decode_audio(self, data: bytes) -> tuple[np.ndarray, int]:
        """decode audio bytes to numpy array.

        tries WAV first, falls back to writing temp file for other formats.
        """
        try:
            # try WAV
            buf = io.BytesIO(data)
            with wave.open(buf, "rb") as wf:
                frames = wf.readframes(wf.getnframes())
                width = wf.getsampwidth()
                channels = wf.getnchannels()
                sr = wf.getframerate()

                if width == 2:
                    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                elif width == 4:
                    samples = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
                else:
                    samples = np.frombuffer(frames, dtype=np.float32)

                if channels == 1:
                    samples = np.column_stack([samples, samples])
                elif channels == 2:
                    samples = samples.reshape(-1, 2)

                return samples.astype(np.float32, copy=False), int(sr)
        except (wave.Error, struct.error):
            pass

        # try soundfile (handles MP3, FLAC, OGG, etc.)
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
            pass

        # fail loudly instead of returning silent zeros at wrong sample rate
        from oram.gateway.base import GenerationFailedError
        raise GenerationFailedError(
            prompt="[sfx decode]",
            status=0,
            body=f"failed to decode {len(data)} bytes of audio",
        )

    def _estimate_cost(self, duration: float) -> float:
        """rough cost estimate in credits."""
        # ElevenLabs charges ~40 credits per second for specified duration
        return round(duration * 40, 1)
