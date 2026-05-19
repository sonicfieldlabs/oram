"""oram.gateway.voice — ElevenLabs Voice adapters.

supports:
- Text-to-Speech (TTS): text → speech
- Speech-to-Speech (Voice Changer): audio → audio with different voice
"""

from __future__ import annotations

import io
import os

import numpy as np

from oram.gateway.base import EngineResult
from oram.gateway.client import ElevenLabsHTTPClient

API_BASE = "https://api.elevenlabs.io/v1"


class VoiceAdapter:
    """generates voice audio using ElevenLabs TTS or Voice Changer."""

    engine_name = "voice"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, prompt: str, params: dict | None = None) -> EngineResult:
        """generate voice audio from text.

        params:
            voice_id: str (default: premade voice)
            model_id: str (default: eleven_flash_v2_5)
            stability: float (0.0-1.0, default 0.5)
            similarity_boost: float (0.0-1.0, default 0.75)
            style: float (0.0-1.0, default 0.0)
            speed: float (0.5-2.0, default 1.0)
        """
        params = params or {}
        voice_id = params.get("voice_id", "21m00Tcm4TlvDq8ikWAM")  # Rachel
        model_id = params.get("model_id", "eleven_flash_v2_5")

        body = {
            "text": prompt,
            "model_id": model_id,
            "voice_settings": {
                "stability": params.get("stability", 0.5),
                "similarity_boost": params.get("similarity_boost", 0.75),
                "style": params.get("style", 0.0),
            },
        }

        client = ElevenLabsHTTPClient(
            self._api_key,
            timeout=60.0,
            output_format=params.get("output_format"),
        )
        response = client.post_json(f"/text-to-speech/{voice_id}", body)

        audio, sample_rate = self._decode_audio(response.content)

        return EngineResult(
            audio=audio,
            sample_rate=sample_rate,
            engine="voice",
            prompt_used=prompt,
            parameters={**body, "voice_id": voice_id},
            cost_credits=client.parse_cost_header(response) or len(prompt),
        )

    def speech_to_speech(
        self,
        source_audio: np.ndarray,
        sample_rate: int,
        params: dict | None = None,
    ) -> EngineResult:
        """change voice identity while preserving performance.

        uses the /v1/speech-to-speech/{voice_id} endpoint.
        """
        import soundfile as sf

        params = params or {}
        voice_id = params.get("voice_id", "21m00Tcm4TlvDq8ikWAM")

        # encode source to WAV bytes
        buf = io.BytesIO()
        mono = np.mean(source_audio, axis=1) if source_audio.ndim > 1 else source_audio
        sf.write(buf, mono, sample_rate, format="WAV", subtype="PCM_16")
        buf.seek(0)

        client = ElevenLabsHTTPClient(
            self._api_key,
            timeout=60.0,
            output_format=params.get("output_format"),
        )
        response = client.post_multipart(
            f"/speech-to-speech/{voice_id}",
            files={"audio": ("audio.wav", buf, "audio/wav")},
            data={
                "model_id": params.get("model_id", "eleven_english_sts_v2"),
            },
        )

        audio, output_sr = self._decode_audio(response.content)

        return EngineResult(
            audio=audio,
            sample_rate=output_sr,
            engine="voice_changer",
            prompt_used="[speech-to-speech]",
            parameters={"voice_id": voice_id},
            cost_credits=client.parse_cost_header(response) or float(len(source_audio) / sample_rate * 100),
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
                prompt="[voice decode]",
                status=0,
                body=f"failed to decode {len(data)} bytes of audio",
            )
