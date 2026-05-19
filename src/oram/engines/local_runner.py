"""oram.engines.local_runner — local audio generation engines.

wraps local generators (MockSoundGenerator and future Python sidecar models)
as OramEngineAdapter implementations. no API keys needed.

engines:
- LocalMockEngine: wraps the existing MockSoundGenerator for keyword-based synthesis
- LocalSidecarEngine: placeholder for the Python FastAPI sidecar (Kokoro, TangoFlux, etc.)
"""

from __future__ import annotations

import logging
import os

import numpy as np

from oram.engines.adapter import EngineSpec, GenerationRequest, GenerationResult
from oram.engines.capabilities import AudioCapability, EngineMode, EngineProvider

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mock Sound Generator (always available, no deps)
# ---------------------------------------------------------------------------

class LocalMockEngine:
    """wraps oram.summon.mock.MockSoundGenerator as an OramEngineAdapter.

    generates procedural synthetic textures from keyword prompts:
    rain, drone, room tone, machine, forest.
    always available — the zero-dependency fallback engine.
    """

    spec = EngineSpec(
        id="local-mock",
        provider=EngineProvider.LOCAL,
        label="ORAM Procedural Generator",
        mode=EngineMode.LOCAL,
        capabilities=[
            AudioCapability.TEXT_TO_SOUND_EFFECT,
            AudioCapability.TEXT_TO_MUSIC,
        ],
        requires_api_key=False,
        supports_streaming=False,
        supports_seed=False,
        supports_audio_input=False,
        max_duration_seconds=120.0,
        cost_per_second=0.0,
        latency_profile="fast",
    )

    def __init__(self, sample_rate: int = 48000):
        self._sample_rate = sample_rate
        self._generator = None

    def _get_generator(self):
        if self._generator is None:
            from oram.summon.mock import MockSoundGenerator
            self._generator = MockSoundGenerator()
        return self._generator

    def is_available(self) -> bool:
        return True  # always available

    def generate(self, request: GenerationRequest) -> GenerationResult:
        generator = self._get_generator()
        duration = min(request.duration_seconds, self.spec.max_duration_seconds)
        audio = generator.generate(request.prompt, duration, self._sample_rate)
        return GenerationResult(
            audio=audio,
            sample_rate=self._sample_rate,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=request.prompt,
            duration_seconds=duration,
            cost_credits=0.0,
            cost_currency="free",
            metadata={"mode": "procedural_synthesis"},
        )


# ---------------------------------------------------------------------------
# Python Sidecar (future: Kokoro, TangoFlux, Whisper, Essentia)
# ---------------------------------------------------------------------------

class LocalSidecarEngine:
    """adapter for the Python FastAPI sidecar.

    the sidecar is a separate process running local ML models:
    - Kokoro/Piper for TTS
    - TangoFlux for SFX
    - AudioLDM2 for music
    - Whisper.cpp for STT
    - Essentia for audio analysis

    this adapter communicates with the sidecar via HTTP on localhost.

    HEALTH CHECK:
    - checks if sidecar is running at the configured host:port
    - verifies the requested model is loaded
    - reports latency for routing decisions
    """

    # default sidecar configuration
    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 7860

    def __init__(
        self,
        model_id: str = "tangoflux",
        host: str | None = None,
        port: int | None = None,
        capabilities: list[AudioCapability] | None = None,
    ):
        self._model_id = model_id
        self._host = host or os.environ.get("ORAM_SIDECAR_HOST", self.DEFAULT_HOST)
        self._port = port or int(os.environ.get("ORAM_SIDECAR_PORT", str(self.DEFAULT_PORT)))
        self._base_url = f"http://{self._host}:{self._port}"
        self._healthy: bool | None = None  # cached health check result
        self._last_health_check: float = 0.0

        # build spec dynamically based on model
        caps = capabilities or self._default_capabilities()
        self.spec = EngineSpec(
            id=f"local-{model_id}",
            provider=EngineProvider.LOCAL,
            label=f"Local {model_id.title()}",
            mode=EngineMode.LOCAL,
            capabilities=caps,
            requires_api_key=False,
            supports_streaming=False,
            supports_seed=True,
            supports_audio_input=model_id in ("whisper", "essentia"),
            max_duration_seconds=60.0 if model_id != "whisper" else 3600.0,
            cost_per_second=0.0,
            latency_profile="slow",
        )

    def _default_capabilities(self) -> list[AudioCapability]:
        """infer capabilities from model_id."""
        model_caps = {
            "tangoflux": [AudioCapability.TEXT_TO_SOUND_EFFECT],
            "audioldm2": [AudioCapability.TEXT_TO_MUSIC, AudioCapability.TEXT_TO_SOUND_EFFECT],
            "kokoro": [AudioCapability.TEXT_TO_SPEECH],
            "piper": [AudioCapability.TEXT_TO_SPEECH],
            "whisper": [AudioCapability.SPEECH_TO_TEXT],
            "essentia": [AudioCapability.AUDIO_ANALYSIS],
        }
        return model_caps.get(self._model_id, [AudioCapability.TEXT_TO_SOUND_EFFECT])

    def is_available(self) -> bool:
        """check if the sidecar is running and the model is loaded.

        caches result for 30 seconds to avoid hammering the sidecar.
        """
        import time

        now = time.monotonic()
        if self._healthy is not None and (now - self._last_health_check) < 30.0:
            return self._healthy

        try:
            import httpx
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self._base_url}/health")
                data = resp.json()
                models = data.get("models", [])
                self._healthy = self._model_id in models
        except Exception:
            self._healthy = False

        self._last_health_check = now
        return self._healthy

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """send generation request to the sidecar.

        POST /generate with JSON payload, receives WAV audio bytes.
        """
        import httpx

        payload = {
            "model": self._model_id,
            "prompt": request.prompt,
            "duration_seconds": min(request.duration_seconds, self.spec.max_duration_seconds),
        }

        if request.seed is not None:
            payload["seed"] = request.seed
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt

        # audio input for analysis/STT models
        if request.source_audio is not None:
            import base64
            import io
            import soundfile as sf

            buf = io.BytesIO()
            mono = (
                np.mean(request.source_audio, axis=1)
                if request.source_audio.ndim > 1
                else request.source_audio
            )
            sf.write(buf, mono, request.source_sample_rate, format="WAV", subtype="PCM_16")
            buf.seek(0)
            payload["audio_base64"] = base64.b64encode(buf.read()).decode("ascii")
            payload["sample_rate"] = request.source_sample_rate

        try:
            with httpx.Client(timeout=180.0) as client:
                response = client.post(
                    f"{self._base_url}/generate",
                    json=payload,
                )
                response.raise_for_status()
        except Exception as e:
            raise RuntimeError(f"sidecar request failed: {e}") from e

        content_type = response.headers.get("content-type", "")

        # JSON response (analysis/STT)
        if "application/json" in content_type:
            data = response.json()
            return GenerationResult(
                audio=np.zeros((0, 2), dtype=np.float32),
                sample_rate=48000,
                engine_id=self.spec.id,
                provider=self.spec.provider.value,
                prompt_used=request.prompt,
                metadata=data,
            )

        # audio response
        import io
        import soundfile as sf

        buf = io.BytesIO(response.content)
        audio, sr = sf.read(buf)
        if audio.ndim == 1:
            audio = np.column_stack([audio, audio])
        audio = audio.astype(np.float32)

        return GenerationResult(
            audio=audio,
            sample_rate=sr,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=request.prompt,
            duration_seconds=len(audio) / sr,
            cost_credits=0.0,
            cost_currency="free",
            metadata={
                "mode": "local_sidecar",
                "model": self._model_id,
            },
        )
