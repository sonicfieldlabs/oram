"""Stable Audio adapters for ORAM.

This module contains two adapters:
- StabilityStableAudioEngine: direct Stability AI Stable Audio API.
- FalStableAudioEngine: Stable Audio through fal.ai, kept for compatibility.

supports:
- text-to-music: generate music from text descriptions
- text-to-sound-effect: generate SFX from text descriptions
- seed control for reproducible outputs
- negative prompts for exclusion control
"""

from __future__ import annotations

import logging
import os
from base64 import b64decode
from io import BytesIO
from urllib.parse import urlparse

import numpy as np

from oram.engines.adapter import EngineSpec, GenerationRequest, GenerationResult
from oram.engines.capabilities import AudioCapability, EngineMode, EngineProvider
from oram_security.network import is_url_allowed
from oram_security.redaction import redact_text

log = logging.getLogger(__name__)


class StabilityStableAudioEngine:
    """Generate audio through the direct Stability AI Stable Audio API."""

    spec = EngineSpec(
        id="stability-stable-audio-2",
        provider=EngineProvider.STABILITY,
        label="Stable Audio 2.5",
        mode=EngineMode.CLOUD,
        capabilities=[
            AudioCapability.TEXT_TO_MUSIC,
            AudioCapability.TEXT_TO_SOUND_EFFECT,
        ],
        requires_api_key=True,
        supports_streaming=False,
        supports_seed=True,
        supports_audio_input=False,
        max_duration_seconds=190.0,
        cost_per_second=0.0,
        latency_profile="slow",
    )

    API_URL = "https://api.stability.ai/v2beta/audio/stable-audio-2/text-to-audio"

    def __init__(self, api_key: str = "", model: str = "stable-audio-2.5"):
        self._api_key = api_key or os.environ.get("STABILITY_API_KEY", "")
        self._model = model

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate a WAV file from text through Stability's multipart API."""
        import httpx

        if not is_url_allowed(self.API_URL):
            host = urlparse(self.API_URL).hostname or "unknown"
            raise RuntimeError(f"Stable Audio host is not in ORAM_NETWORK_ALLOWLIST: {host}")

        duration = min(max(float(request.duration_seconds), 1.0), self.spec.max_duration_seconds)
        steps = int(request.parameters.get("steps", 8))
        cfg_scale = float(request.guidance_scale or request.parameters.get("cfg_scale", 7.0))
        model = request.model_id or request.parameters.get("model", self._model)

        data: dict[str, str] = {
            "prompt": request.prompt,
            "duration": str(round(duration, 3)),
            "steps": str(steps),
            "cfg_scale": str(cfg_scale),
            "model": str(model),
            "output_format": "wav",
        }
        if request.seed is not None:
            data["seed"] = str(request.seed)
        if request.negative_prompt:
            data["negative_prompt"] = request.negative_prompt

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "audio/*",
        }

        try:
            with httpx.Client(timeout=240.0) as client:
                response = client.post(
                    self.API_URL,
                    headers=headers,
                    data=data,
                    files={"none": ("", b"")},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = redact_text(exc.response.text[:300])
            log.error("Stability API error: %s %s", exc.response.status_code, body)
            raise RuntimeError(f"Stable Audio generation failed: {exc.response.status_code}") from exc
        except Exception as exc:
            log.error("Stability request failed: %s", redact_text(exc))
            raise RuntimeError(f"Stable Audio generation failed: {redact_text(exc)}") from exc

        audio, sample_rate = self._parse_response(response)

        return GenerationResult(
            audio=audio,
            sample_rate=sample_rate,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=request.prompt,
            duration_seconds=len(audio) / sample_rate if sample_rate > 0 else 0,
            parameters={
                "duration": duration,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "seed": request.seed,
                "model": model,
                "output_format": "wav",
            },
            metadata={
                "mode": "text_to_audio",
                "api": "stability",
                "endpoint": "/v2beta/audio/stable-audio-2/text-to-audio",
            },
        )

    def _parse_response(self, response) -> tuple[np.ndarray, int]:
        content_type = response.headers.get("content-type", "").lower()
        if "audio" in content_type or response.content.startswith(b"RIFF"):
            return _decode_audio_bytes(response.content)

        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError("Stable Audio returned a non-audio response") from exc

        if isinstance(data, dict):
            audio_b64 = (
                data.get("audio")
                or data.get("audio_base64")
                or data.get("base64")
            )
            if isinstance(audio_b64, str) and audio_b64:
                return _decode_audio_bytes(b64decode(audio_b64))

            artifacts = data.get("artifacts")
            if isinstance(artifacts, list) and artifacts:
                first = artifacts[0] if isinstance(artifacts[0], dict) else {}
                artifact_b64 = first.get("base64") or first.get("audio")
                if isinstance(artifact_b64, str) and artifact_b64:
                    return _decode_audio_bytes(b64decode(artifact_b64))

            url = data.get("url") or (data.get("audio_file") or {}).get("url")
            if isinstance(url, str) and url:
                if not is_url_allowed(url):
                    host = urlparse(url).hostname or "unknown"
                    raise RuntimeError(f"Stable Audio returned an unallowlisted URL: {host}")
                return _download_audio(url)

        raise RuntimeError("Stable Audio returned no audio")


class FalStableAudioEngine:
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
            log.error("fal API error: %s %s", e.response.status_code, redact_text(e.response.text[:200]))
            raise RuntimeError(f"Stable Audio generation failed: {e.response.status_code}") from e
        except Exception as e:
            log.error("fal request failed: %s", redact_text(e))
            raise RuntimeError(f"Stable Audio generation failed: {redact_text(e)}") from e

        # parse response — fal returns audio_file with url
        audio_file = data.get("audio_file", {})
        audio_url = audio_file.get("url", "")

        if not audio_url:
            raise RuntimeError("Stable Audio returned no audio URL")

        # download the generated audio
        audio, sample_rate = _download_audio(audio_url)

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


def _decode_audio_bytes(content: bytes) -> tuple[np.ndarray, int]:
    import soundfile as sf

    buf = BytesIO(content)
    audio, sr = sf.read(buf)

    if audio.ndim == 1:
        audio = np.column_stack([audio, audio])
    elif audio.ndim == 2 and audio.shape[1] > 2:
        audio = audio[:, :2]

    return audio.astype(np.float32), int(sr)


def _download_audio(url: str) -> tuple[np.ndarray, int]:
    """download and decode audio from a URL."""
    import httpx

    with httpx.Client(timeout=60.0) as client:
        response = client.get(url)
        response.raise_for_status()

    return _decode_audio_bytes(response.content)


# Backward-compatible name used by existing tests and imports.
StableAudioEngine = FalStableAudioEngine
