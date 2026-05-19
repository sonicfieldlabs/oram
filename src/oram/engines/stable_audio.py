"""oram.engines.stable_audio — Stable Audio adapter via fal.ai.

generates audio using Stable Audio Open / Stable Audio 2.5
through the fal.ai serverless inference platform.

supports:
- text-to-music: generate music from text descriptions
- text-to-sound-effect: generate SFX from text descriptions
- audio-to-audio: condition generation on source audio (style transfer)
- seed control for reproducible outputs
- negative prompts for exclusion control
"""

from __future__ import annotations

import logging
import os
from io import BytesIO

import numpy as np

from oram.engines.adapter import EngineSpec, GenerationRequest, GenerationResult
from oram.engines.capabilities import AudioCapability, EngineMode, EngineProvider

log = logging.getLogger(__name__)


class StableAudioEngine:
    """generates audio using Stable Audio via fal.ai.

    requires FAL_KEY environment variable or api_key parameter.
    uses the fal-ai/stable-audio endpoint.
    """

    spec = EngineSpec(
        id="stable-audio-25",
        provider=EngineProvider.FAL,
        label="Stable Audio 2.5 via fal",
        mode=EngineMode.CLOUD,
        capabilities=[
            AudioCapability.TEXT_TO_MUSIC,
            AudioCapability.TEXT_TO_SOUND_EFFECT,
            AudioCapability.AUDIO_TO_AUDIO,
        ],
        requires_api_key=True,
        supports_streaming=False,
        supports_seed=True,
        supports_audio_input=True,
        max_duration_seconds=47.0,
        cost_per_second=5.0,
        latency_profile="medium",
    )

    # fal endpoint for Stable Audio
    FAL_ENDPOINT = "fal-ai/stable-audio"

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or os.environ.get("FAL_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """generate audio via fal.ai Stable Audio endpoint.

        supports text-to-audio and audio-conditioned generation.
        """
        import httpx

        # build fal request payload
        payload: dict = {
            "prompt": request.prompt,
            "seconds_total": min(request.duration_seconds, self.spec.max_duration_seconds),
            "steps": request.parameters.get("steps", 100),
        }

        # seed for reproducibility
        if request.seed is not None:
            payload["seed"] = request.seed

        # negative prompt
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt

        # guidance scale
        if request.guidance_scale is not None:
            payload["cfg_scale"] = request.guidance_scale
        else:
            payload["cfg_scale"] = request.parameters.get("cfg_scale", 7.0)

        # audio-to-audio: encode source audio as base64 WAV
        if request.source_audio is not None:
            payload["audio_url"] = self._encode_source_audio(
                request.source_audio, request.source_sample_rate
            )
            # strength controls how much the source influences output
            payload["strength"] = request.parameters.get("strength", 0.7)

        # call fal.ai
        headers = {
            "Authorization": f"Key {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    f"https://fal.run/{self.FAL_ENDPOINT}",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            log.error("fal API error: %s %s", e.response.status_code, e.response.text[:200])
            raise RuntimeError(f"Stable Audio generation failed: {e.response.status_code}") from e
        except Exception as e:
            log.error("fal request failed: %s", e)
            raise RuntimeError(f"Stable Audio generation failed: {e}") from e

        # parse response — fal returns audio_file with url
        audio_file = data.get("audio_file", {})
        audio_url = audio_file.get("url", "")

        if not audio_url:
            raise RuntimeError("Stable Audio returned no audio URL")

        # download the generated audio
        audio, sample_rate = self._download_audio(audio_url)

        return GenerationResult(
            audio=audio,
            sample_rate=sample_rate,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=request.prompt,
            duration_seconds=len(audio) / sample_rate if sample_rate > 0 else 0,
            cost_credits=payload["seconds_total"] * self.spec.cost_per_second,
            cost_currency="fal_credits",
            parameters={
                "steps": payload.get("steps"),
                "cfg_scale": payload.get("cfg_scale"),
                "seed": payload.get("seed"),
            },
            metadata={
                "mode": "audio_to_audio" if request.source_audio is not None else "text_to_audio",
                "fal_endpoint": self.FAL_ENDPOINT,
                "negative_prompt": request.negative_prompt or "",
            },
        )

    def _encode_source_audio(self, audio: np.ndarray, sample_rate: int) -> str:
        """encode source audio as a data URI for fal.ai audio input."""
        import base64
        import soundfile as sf

        mono = np.mean(audio, axis=1) if audio.ndim > 1 else audio
        buf = BytesIO()
        sf.write(buf, mono, sample_rate, format="WAV", subtype="PCM_16")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("ascii")
        return f"data:audio/wav;base64,{b64}"

    def _download_audio(self, url: str) -> tuple[np.ndarray, int]:
        """download and decode audio from a URL."""
        import httpx
        import soundfile as sf

        with httpx.Client(timeout=60.0) as client:
            response = client.get(url)
            response.raise_for_status()

        buf = BytesIO(response.content)
        audio, sr = sf.read(buf)

        if audio.ndim == 1:
            audio = np.column_stack([audio, audio])
        elif audio.ndim == 2 and audio.shape[1] > 2:
            audio = audio[:, :2]

        return audio.astype(np.float32), int(sr)
