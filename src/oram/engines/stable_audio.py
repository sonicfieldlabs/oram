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

import json
import logging
import os
import tempfile
from base64 import b64decode, b64encode
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import numpy as np

from oram.engines.adapter import EngineSpec, GenerationRequest, GenerationResult
from oram.engines.capabilities import AudioCapability, EngineMode, EngineProvider
from oram_security.network import is_url_allowed
from oram_security.redaction import redact_text

log = logging.getLogger(__name__)


SA3_MODES = frozenset({
    "generate",
    "morph",
    "continue",
    "inpaint",
    "latent",
    "lora_mixer",
})

LOCAL_STABLE_AUDIO_INPUT_SAMPLE_RATE = 44100

SA3_MODE_ALIASES = {
    "audio_to_audio": "morph",
    "audio-to-audio": "morph",
    "transform": "morph",
    "replace": "inpaint",
    "continuation": "continue",
    "extend": "continue",
    "lora": "lora_mixer",
    "loramixer": "lora_mixer",
}


def _stable_audio3_capabilities() -> list[AudioCapability]:
    return [
        AudioCapability.TEXT_TO_SOUND_EFFECT,
        AudioCapability.TEXT_TO_MUSIC,
        AudioCapability.AUDIO_TO_AUDIO,
        AudioCapability.AUDIO_INPAINTING,
        AudioCapability.AUDIO_CONTINUATION,
        AudioCapability.LORA_MIXING,
        AudioCapability.AUDIO_LATENT,
    ]


class StabilityStableAudioEngine:
    """Generate audio through the direct Stability AI Stable Audio API."""

    # NOTE: The YAML catalog (engines.yaml) uses "stable-audio-25-fal" for the
    # fal variant. This direct-API engine doesn't yet have a YAML entry;
    # the ID below is planned to be added in a future catalog update.
    spec = EngineSpec(
        id="stability-stable-audio-25",
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
        steps = int(request.parameters.get("steps", 50))
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
                # Pass form fields via `data=` — httpx encodes as
                # multipart/form-data automatically when `data` is a dict.
                response = client.post(
                    self.API_URL,
                    headers=headers,
                    data=data,
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
        tried: list[str] = []
        content_type = response.headers.get("content-type", "").lower()
        if "audio" in content_type or response.content.startswith(b"RIFF"):
            tried.append("raw-audio-bytes")
            log.debug("_parse_response: matched raw audio bytes")
            return _decode_audio_bytes(response.content)

        tried.append("raw-audio-bytes(no match)")

        try:
            data = response.json()
        except Exception as exc:
            log.debug("_parse_response: tried paths %s before JSON parse failure", tried)
            raise RuntimeError("Stable Audio returned a non-audio response") from exc

        if isinstance(data, dict):
            audio_b64 = (
                data.get("audio")
                or data.get("audio_base64")
                or data.get("base64")
            )
            if isinstance(audio_b64, str) and audio_b64:
                tried.append("json-base64-field")
                log.debug("_parse_response: matched base64 audio field")
                return _decode_audio_bytes(b64decode(audio_b64))
            tried.append("json-base64-field(no match)")

            artifacts = data.get("artifacts")
            if isinstance(artifacts, list) and artifacts:
                first = artifacts[0] if isinstance(artifacts[0], dict) else {}
                artifact_b64 = first.get("base64") or first.get("audio")
                if isinstance(artifact_b64, str) and artifact_b64:
                    tried.append("json-artifacts[0]-base64")
                    log.debug("_parse_response: matched artifacts[0] base64")
                    return _decode_audio_bytes(b64decode(artifact_b64))
            tried.append("json-artifacts(no match)")

            url = data.get("url") or (data.get("audio_file") or {}).get("url")
            if isinstance(url, str) and url:
                tried.append("json-url")
                if not is_url_allowed(url):
                    host = urlparse(url).hostname or "unknown"
                    raise RuntimeError(f"Stable Audio returned an unallowlisted URL: {host}")
                log.debug("_parse_response: matched url field -> downloading")
                return _download_audio(url)
            tried.append("json-url(no match)")

        log.debug("_parse_response: all paths exhausted, tried: %s", tried)
        raise RuntimeError(f"Stable Audio returned no audio (tried: {', '.join(tried)})")


class StabilityStableAudio3Engine:
    """Stable Audio 3 Large through Stability's API.

    The public API route has changed across Stable Audio releases, so ORAM lets
    operators override it with ORAM_STABLE_AUDIO_API_URL while keeping a
    provider-compatible request payload.
    """

    DEFAULT_API_URL = "https://api.stability.ai/v2beta/audio/stable-audio-3/generate"

    spec = EngineSpec(
        id="stability-stable-audio-3",
        provider=EngineProvider.STABILITY,
        label="Stable Audio 3 Large",
        mode=EngineMode.CLOUD,
        capabilities=_stable_audio3_capabilities(),
        requires_api_key=True,
        supports_streaming=False,
        supports_seed=True,
        supports_audio_input=True,
        max_duration_seconds=380.0,
        cost_per_second=0.0,
        latency_profile="slow",
    )

    def __init__(self, api_key: str = "", api_url: str = "", model: str = "large"):
        self._api_key = api_key or os.environ.get("STABILITY_API_KEY", "")
        self._api_url = (
            api_url
            or os.environ.get("ORAM_STABLE_AUDIO_API_URL", "")
            or self.DEFAULT_API_URL
        ).rstrip("/")
        self._model = model

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        import httpx

        if not is_url_allowed(self._api_url):
            host = urlparse(self._api_url).hostname or "unknown"
            raise RuntimeError(f"Stable Audio host is not in ORAM_NETWORK_ALLOWLIST: {host}")

        payload = _build_stable_audio3_payload(
            request,
            provider_backend="stability_api",
            model=request.model_id or str(request.parameters.get("model", self._model)),
            decoder=str(request.parameters.get("decoder", "same-l")),
            max_duration=self.spec.max_duration_seconds,
        )
        data = _multipart_data(payload)
        files = _multipart_audio_files(request)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "audio/*, application/json",
        }

        try:
            with httpx.Client(timeout=360.0) as client:
                response = client.post(
                    self._api_url,
                    headers=headers,
                    data=data,
                    files=files or None,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = redact_text(exc.response.text[:300])
            log.error("Stable Audio 3 API error: %s %s", exc.response.status_code, body)
            raise RuntimeError(f"Stable Audio 3 generation failed: {exc.response.status_code}") from exc
        except Exception as exc:
            log.error("Stable Audio 3 request failed: %s", redact_text(exc))
            raise RuntimeError(f"Stable Audio 3 generation failed: {redact_text(exc)}") from exc

        audio, sample_rate = _parse_audio_response(response, allow_local_paths=False)
        return _stable_audio3_result(
            request=request,
            spec=self.spec,
            audio=audio,
            sample_rate=sample_rate,
            payload=payload,
            endpoint=urlparse(self._api_url).path,
        )


class LocalStableAudio3Engine:
    """Stable Audio 3 local-service adapter.

    This talks to an ORAM-compatible sidecar service such as an MLX wrapper,
    Python/CUDA service, or Max/Ableton helper. Rendering remains outside the
    realtime audio thread; ORAM receives a WAV/JSON response and stores the
    returned clip in the library.
    """

    spec = EngineSpec(
        id="stable-audio-3-local",
        provider=EngineProvider.LOCAL,
        label="Stable Audio 3 Local Service",
        mode=EngineMode.LOCAL,
        capabilities=_stable_audio3_capabilities(),
        requires_api_key=False,
        supports_streaming=False,
        supports_seed=True,
        supports_audio_input=True,
        max_duration_seconds=380.0,
        cost_per_second=0.0,
        latency_profile="slow",
    )

    def __init__(
        self,
        base_url: str = "",
        *,
        provider_backend: str = "stable_audio_mlx",
        model: str = "sm-music",
        decoder: str = "same-s",
    ):
        self._base_url = (base_url or os.environ.get("ORAM_STABLE_AUDIO_SERVICE_URL", "")).rstrip("/")
        self._provider_backend = _normalize_local_provider(
            os.environ.get("ORAM_STABLE_AUDIO_LOCAL_PROVIDER", provider_backend)
        )
        self._model = os.environ.get("ORAM_STABLE_AUDIO_LOCAL_MODEL", model)
        self._decoder = os.environ.get("ORAM_STABLE_AUDIO_DECODER", decoder)
        self._healthy: bool | None = None
        self._last_health_check = 0.0

    def is_available(self) -> bool:
        if not self._base_url:
            return False

        import time

        now = time.monotonic()
        if self._healthy is not None and (now - self._last_health_check) < 30.0:
            return self._healthy

        try:
            import httpx

            if not _local_or_allowlisted_url(self._base_url):
                raise RuntimeError("local Stable Audio service URL is not allowed")

            with httpx.Client(timeout=0.75) as client:
                response = client.get(f"{self._base_url}/health")
                self._healthy = 200 <= response.status_code < 500
        except Exception:
            self._healthy = False

        self._last_health_check = now
        return self._healthy

    def generate(self, request: GenerationRequest) -> GenerationResult:
        import httpx

        base_url = str(request.parameters.get("service_url") or self._base_url).rstrip("/")
        if not base_url:
            raise RuntimeError("ORAM_STABLE_AUDIO_SERVICE_URL is not configured")
        if not _local_or_allowlisted_url(base_url):
            host = urlparse(base_url).hostname or "unknown"
            raise RuntimeError(f"Stable Audio local service host is not allowed: {host}")

        provider_backend = _normalize_local_provider(
            str(
                request.parameters.get("local_provider")
                or request.parameters.get("provider_backend")
                or request.parameters.get("backend")
                or self._provider_backend
            )
        )
        model = _normalize_local_model(
            str(
                request.parameters.get("local_model")
                or request.parameters.get("sa3_model")
                or request.model_id
                or request.parameters.get("model")
                or self._model
            ),
            provider_backend,
        )
        payload = _build_stable_audio3_payload(
            request,
            provider_backend=provider_backend,
            model=model,
            decoder=str(request.parameters.get("decoder", self._decoder)),
            max_duration=self.spec.max_duration_seconds,
        )
        if "chunked_decode" in request.parameters:
            payload["chunked_decode"] = _coerce_bool(request.parameters.get("chunked_decode"), default=True)

        mode = payload["mode"]
        try:
            with httpx.Client(timeout=360.0) as client:
                response = client.post(f"{base_url}/render", json=payload)
                if response.status_code == 404:
                    response = _post_local_mode_render(client, base_url, payload)
                if response.status_code == 404:
                    response = client.post(f"{base_url}/{mode}", json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = redact_text(exc.response.text[:300])
            log.error("Stable Audio local service error: %s %s", exc.response.status_code, body)
            raise RuntimeError(f"Stable Audio local generation failed: {exc.response.status_code}") from exc
        except Exception as exc:
            log.error("Stable Audio local request failed: %s", redact_text(exc))
            raise RuntimeError(f"Stable Audio local generation failed: {redact_text(exc)}") from exc

        audio, sample_rate = _parse_audio_response(response, allow_local_paths=True, base_url=base_url)
        return _stable_audio3_result(
            request=request,
            spec=self.spec,
            audio=audio,
            sample_rate=sample_rate,
            payload=payload,
            endpoint=f"{base_url}/render",
        )


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

    _MAX_AUDIO_INPUT_BYTES = 10 * 1024 * 1024  # 10 MB

    def _encode_source_audio(self, audio: np.ndarray, sample_rate: int) -> str:
        """encode source audio as a data URI for fal.ai audio input."""
        import base64

        import soundfile as sf

        mono = np.mean(audio, axis=1) if audio.ndim > 1 else audio
        buf = BytesIO()
        sf.write(buf, mono, sample_rate, format="WAV", subtype="PCM_16")
        wav_size = buf.tell()

        if wav_size > self._MAX_AUDIO_INPUT_BYTES:
            max_samples = int(
                (self._MAX_AUDIO_INPUT_BYTES / wav_size) * len(mono)
            )
            log.warning(
                "Source audio WAV is %d bytes (> %d MB limit); truncating to %d samples",
                wav_size,
                self._MAX_AUDIO_INPUT_BYTES // (1024 * 1024),
                max_samples,
            )
            mono = mono[:max_samples]
            buf = BytesIO()
            sf.write(buf, mono, sample_rate, format="WAV", subtype="PCM_16")

        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("ascii")
        return f"data:audio/wav;base64,{b64}"


def _normalize_local_provider(value: str) -> str:
    provider = str(value or "stable_audio_mlx").strip().lower().replace("-", "_")
    aliases = {
        "local": "stable_audio_mlx",
        "local_mlx": "stable_audio_mlx",
        "mlx": "stable_audio_mlx",
        "stable_audio3_mlx": "stable_audio_mlx",
        "local_python": "stable_audio_python",
        "python": "stable_audio_python",
        "cuda": "stable_audio_python",
    }
    return aliases.get(provider, provider)


def _normalize_local_model(model: str, provider: str) -> str:
    value = str(model or "").strip()
    if not value:
        return "sm-music" if provider == "stable_audio_mlx" else "small-music"
    if provider == "stable_audio_mlx":
        return {
            "small-music": "sm-music",
            "small-sfx": "sm-sfx",
            "small_music": "sm-music",
            "small_sfx": "sm-sfx",
        }.get(value, value)
    if provider == "stable_audio_python":
        return {
            "sm-music": "small-music",
            "sm-sfx": "small-sfx",
            "small_music": "small-music",
            "small_sfx": "small-sfx",
            "medium-mlx": "medium",
        }.get(value, value)
    return value


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_sa3_mode(value: Any, *, has_source: bool = False) -> str:
    mode = str(value or ("morph" if has_source else "generate")).strip().lower().replace("-", "_")
    mode = SA3_MODE_ALIASES.get(mode, mode)
    if mode not in SA3_MODES:
        raise ValueError(f"unsupported Stable Audio mode: {mode}")
    return mode


def _coerce_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _normalize_inpaint_ranges(parameters: dict[str, Any]) -> list[dict[str, float]]:
    raw = parameters.get("inpaint_ranges") or parameters.get("inpaint_range") or []
    ranges: list[dict[str, float]] = []
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, str) and raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) == 2:
            raw = [{"start": float(parts[0]), "end": float(parts[1])}]
        else:
            raw = []

    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                start = item.get("start", item.get("start_seconds"))
                end = item.get("end", item.get("end_seconds"))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                start, end = item[0], item[1]
            else:
                continue
            if start is None or end is None:
                continue
            start_f = float(start)
            end_f = float(end)
            if end_f <= start_f:
                raise ValueError("inpaint range end must be greater than start")
            ranges.append({"start": start_f, "end": end_f})

    start = _coerce_optional_float(
        parameters.get("inpaint_start")
        or parameters.get("inpaint_start_seconds")
        or parameters.get("inpaint_mask_start_seconds")
    )
    end = _coerce_optional_float(
        parameters.get("inpaint_end")
        or parameters.get("inpaint_end_seconds")
        or parameters.get("inpaint_mask_end_seconds")
    )
    if start is not None and end is not None:
        if end <= start:
            raise ValueError("inpaint range end must be greater than start")
        ranges.append({"start": start, "end": end})

    return ranges


def _normalize_lora_stack(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    raw = parameters.get("lora_stack") or []
    stack: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, (list, tuple)):
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("ckpt_path") or item.get("lora_ckpt_path") or "").strip()
            if not path:
                continue
            strength = float(item.get("strength", item.get("diffusion_strength", 1.0)))
            conditioner_strength = float(item.get("conditioner_strength", strength))
            interval = item.get("interval", item.get("lora_interval", [0.0, 1.0]))
            if isinstance(interval, str):
                parts = [p.strip() for p in interval.split(",") if p.strip()]
                interval = [float(parts[0]), float(parts[1])] if len(parts) == 2 else [0.0, 1.0]
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                interval = [0.0, 1.0]
            stack.append({
                "name": str(item.get("name") or f"lora_{index + 1}"),
                "path": path,
                "strength": strength,
                "conditioner_strength": conditioner_strength,
                "interval": [
                    max(0.0, min(float(interval[0]), 1.0)),
                    max(0.0, min(float(interval[1]), 1.0)),
                ],
                "layer_filter": str(item.get("layer_filter", "")),
            })

    interval = parameters.get("lora_interval")
    if isinstance(interval, str):
        parts = [p.strip() for p in interval.split(",") if p.strip()]
        interval = [float(parts[0]), float(parts[1])] if len(parts) == 2 else None
    if not interval:
        interval_min = parameters.get("lora_interval_min", 0.0)
        interval_max = parameters.get("lora_interval_max", 1.0)
        interval = [interval_min, interval_max]

    for suffix, label in (("a", "LoRA A"), ("b", "LoRA B")):
        path = str(
            parameters.get(f"lora_{suffix}_path")
            or parameters.get(f"lora_{suffix}_ckpt_path")
            or ""
        ).strip()
        if not path:
            continue
        strength = float(parameters.get(f"lora_{suffix}_strength", 1.0))
        stack.append({
            "name": label,
            "path": path,
            "strength": strength,
            "conditioner_strength": float(parameters.get(f"lora_{suffix}_conditioner_strength", strength)),
            "interval": [
                max(0.0, min(float(interval[0]), 1.0)),
                max(0.0, min(float(interval[1]), 1.0)),
            ] if isinstance(interval, (list, tuple)) and len(interval) == 2 else [0.0, 1.0],
            "layer_filter": str(parameters.get(f"lora_{suffix}_layer_filter", "")),
        })

    return stack


def _build_stable_audio3_payload(
    request: GenerationRequest,
    *,
    provider_backend: str,
    model: str,
    decoder: str,
    max_duration: float,
) -> dict[str, Any]:
    params = dict(request.parameters or {})
    has_source = request.source_audio is not None or bool(request.source_audio_path or params.get("init_audio_path"))
    mode = _normalize_sa3_mode(
        params.get("stable_audio_mode") or params.get("oram_mode") or params.get("mode"),
        has_source=has_source,
    )
    duration = min(max(float(request.duration_seconds), 0.5), float(max_duration))
    cfg_scale = float(
        request.guidance_scale
        if request.guidance_scale is not None
        else params.get("cfg_scale", params.get("guidance_scale", 1.0))
    )
    seed = request.seed if request.seed is not None else _coerce_optional_int(params.get("seed"))
    negative_prompt = request.negative_prompt if request.negative_prompt is not None else params.get("negative_prompt")
    init_noise_level = _coerce_optional_float(
        params.get("init_noise_level", params.get("noise_depth", params.get("morph_depth")))
    )
    inpaint_ranges = _normalize_inpaint_ranges(params)
    lora_stack = _normalize_lora_stack(params)

    if mode in {"morph", "inpaint", "continue", "latent"} and not has_source:
        raise ValueError(f"Stable Audio mode '{mode}' requires source audio")
    if mode == "inpaint" and not inpaint_ranges:
        raise ValueError("Stable Audio inpaint mode requires an inpaint range")

    payload: dict[str, Any] = {
        "provider": provider_backend,
        "model": model,
        "decoder": decoder,
        "mode": mode,
        "prompt": request.prompt,
        "negative_prompt": negative_prompt or "",
        "duration": duration,
        "seconds_total": duration,
        "steps": int(params.get("steps", 8)),
        "cfg_scale": cfg_scale,
        "seed": seed if seed is not None else -1,
        "init_noise_level": init_noise_level if init_noise_level is not None else (0.55 if mode == "morph" else None),
        "inpaint_ranges": inpaint_ranges,
        "lora_stack": lora_stack,
        "variation_count": int(params.get("variation_count", params.get("batch_size", 1))),
        "chunked_decode": _coerce_bool(params.get("chunked_decode"), default=True),
    }

    if inpaint_ranges:
        payload["inpaint_mask_start_seconds"] = [item["start"] for item in inpaint_ranges]
        payload["inpaint_mask_end_seconds"] = [item["end"] for item in inpaint_ranges]

    source_audio_path = request.source_audio_path or params.get("init_audio_path") or params.get("source_audio_path")
    if source_audio_path:
        payload["init_audio_path"] = str(source_audio_path)
        payload["source_audio_path"] = str(source_audio_path)

    source_audio = request.source_audio
    source_sample_rate = int(request.source_sample_rate)
    if source_audio is not None and provider_backend != "stability_api":
        source_audio, source_sample_rate = _local_stable_audio_source_array(
            source_audio,
            source_sample_rate,
        )

    if source_audio is not None:
        encoded = _audio_to_base64_wav(source_audio, source_sample_rate)
        payload["init_audio_base64"] = encoded
        payload["source_audio_base64"] = encoded
        payload["source_sample_rate"] = source_sample_rate
        if source_sample_rate > 0:
            payload["source_duration"] = len(source_audio) / source_sample_rate

    return payload


def _post_local_mode_render(client, base_url: str, payload: dict[str, Any]):
    """Call Germinator-compatible mode endpoints for local Stable Audio."""
    endpoint, body, temporary_paths = _germinator_request_from_payload(payload)
    try:
        return client.post(f"{base_url}{endpoint}", json=body)
    finally:
        for path in temporary_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


def _germinator_request_from_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any], list[Path]]:
    mode = str(payload.get("mode", "generate"))
    provider = _normalize_local_provider(str(payload.get("provider") or "stable_audio_mlx"))
    model = _normalize_local_model(str(payload.get("model") or ""), provider)
    temporary_paths: list[Path] = []

    body: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "prompt": payload.get("prompt", ""),
        "negative_prompt": payload.get("negative_prompt", ""),
        "duration": payload.get("duration", payload.get("seconds_total", 8.0)),
        "steps": payload.get("steps", 8),
        "cfg_scale": payload.get("cfg_scale", 1.0),
        "seed": payload.get("seed", -1),
        "batch_size": max(1, int(payload.get("variation_count", 1) or 1)),
        "chunked_decode": _coerce_bool(payload.get("chunked_decode"), default=True),
        "lora": _germinator_lora_stack(payload.get("lora_stack", [])),
    }

    if mode in {"generate", "lora_mixer"}:
        return "/generate", body, temporary_paths

    input_audio_path = _payload_input_audio_path(payload, temporary_paths)
    body["input_audio_path"] = input_audio_path

    if mode == "morph":
        body["init_noise_level"] = payload.get("init_noise_level", 0.55)
        return "/audio-to-audio", body, temporary_paths
    if mode == "inpaint":
        body["inpaint_ranges"] = _germinator_ranges(payload.get("inpaint_ranges", []))
        return "/inpaint", body, temporary_paths
    if mode == "continue":
        source_duration = float(payload.get("source_duration") or _audio_file_duration(input_audio_path))
        target_duration = float(payload.get("duration", payload.get("seconds_total", source_duration)))
        if target_duration <= source_duration:
            target_duration = source_duration + max(0.5, target_duration)
        body["source_duration"] = source_duration
        body["target_duration"] = target_duration
        body["duration"] = target_duration
        return "/continue", body, temporary_paths

    raise ValueError(f"local Stable Audio mode '{mode}' is not supported by Germinator")


def _payload_input_audio_path(payload: dict[str, Any], temporary_paths: list[Path]) -> str:
    source_audio_path = payload.get("init_audio_path") or payload.get("source_audio_path")
    if source_audio_path:
        path = Path(str(source_audio_path)).expanduser()
    else:
        encoded = payload.get("init_audio_base64") or payload.get("source_audio_base64")
        if not encoded:
            raise ValueError("local Stable Audio edit mode requires source audio")

        if isinstance(encoded, str) and encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        handle = tempfile.NamedTemporaryFile(prefix="oram_sa3_source_", suffix=".wav", delete=False)
        with handle:
            handle.write(b64decode(str(encoded)))
        path = Path(handle.name)
        temporary_paths.append(path)
    return str(_local_stable_audio_source_path(path, temporary_paths))


def _local_stable_audio_source_array(audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int]:
    from oram.audio.resample import ensure_stereo_float32

    normalized = ensure_stereo_float32(
        np.asarray(audio, dtype=np.float32),
        int(sample_rate),
        LOCAL_STABLE_AUDIO_INPUT_SAMPLE_RATE,
    )
    return normalized, LOCAL_STABLE_AUDIO_INPUT_SAMPLE_RATE


def _local_stable_audio_source_path(path: Path, temporary_paths: list[Path]) -> Path:
    import soundfile as sf

    info = sf.info(str(path))
    if info.samplerate == LOCAL_STABLE_AUDIO_INPUT_SAMPLE_RATE and info.channels == 2:
        return path

    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    normalized, normalized_sample_rate = _local_stable_audio_source_array(audio, int(sample_rate))
    handle = tempfile.NamedTemporaryFile(prefix="oram_sa3_source_44100_", suffix=".wav", delete=False)
    with handle:
        normalized_path = Path(handle.name)
    sf.write(
        str(normalized_path),
        normalized,
        normalized_sample_rate,
        format="WAV",
        subtype="PCM_16",
    )
    temporary_paths.append(normalized_path)
    return normalized_path


def _germinator_ranges(raw_ranges: Any) -> list[list[float]]:
    ranges: list[list[float]] = []
    if isinstance(raw_ranges, str):
        raw_ranges = _normalize_inpaint_ranges({"inpaint_range": raw_ranges})
    if isinstance(raw_ranges, dict):
        raw_ranges = [raw_ranges]
    for item in raw_ranges or []:
        if isinstance(item, dict):
            start = item.get("start", item.get("start_seconds"))
            end = item.get("end", item.get("end_seconds"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            start, end = item[0], item[1]
        else:
            continue
        if start is None or end is None:
            continue
        ranges.append([float(start), float(end)])
    if not ranges:
        raise ValueError("local Stable Audio inpaint mode requires an inpaint range")
    return ranges


def _germinator_lora_stack(raw_stack: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_stack, (list, tuple)):
        return []
    loras = []
    for item in raw_stack:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        loras.append({
            "path": item["path"],
            "strength": item.get("strength"),
        })
    return loras


def _audio_file_duration(path: str) -> float:
    import soundfile as sf

    info = sf.info(str(Path(path).expanduser()))
    if info.samplerate <= 0:
        return 0.0
    return float(info.frames) / float(info.samplerate)


def _stable_audio3_result(
    *,
    request: GenerationRequest,
    spec: EngineSpec,
    audio: np.ndarray,
    sample_rate: int,
    payload: dict[str, Any],
    endpoint: str,
) -> GenerationResult:
    return GenerationResult(
        audio=audio,
        sample_rate=sample_rate,
        engine_id=spec.id,
        provider=spec.provider.value,
        prompt_used=request.prompt,
        duration_seconds=len(audio) / sample_rate if sample_rate > 0 else 0,
        parameters={
            "mode": payload.get("mode"),
            "model": payload.get("model"),
            "decoder": payload.get("decoder"),
            "duration": payload.get("duration"),
            "steps": payload.get("steps"),
            "cfg_scale": payload.get("cfg_scale"),
            "seed": payload.get("seed"),
            "init_noise_level": payload.get("init_noise_level"),
            "variation_count": payload.get("variation_count"),
        },
        metadata={
            "mode": payload.get("mode"),
            "api": payload.get("provider"),
            "endpoint": endpoint,
            "negative_prompt": payload.get("negative_prompt", ""),
            "inpaint_ranges": payload.get("inpaint_ranges", []),
            "lora_stack": [
                {
                    "name": lora.get("name"),
                    "strength": lora.get("strength"),
                    "conditioner_strength": lora.get("conditioner_strength"),
                    "interval": lora.get("interval"),
                }
                for lora in payload.get("lora_stack", [])
            ],
        },
    )


def _audio_to_base64_wav(audio: np.ndarray, sample_rate: int) -> str:
    import soundfile as sf

    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim == 2 and arr.shape[1] > 2:
        arr = arr[:, :2]
    elif arr.ndim == 2 and arr.shape[1] == 1:
        arr = np.column_stack([arr[:, 0], arr[:, 0]])
    buf = BytesIO()
    sf.write(buf, arr, int(sample_rate), format="WAV", subtype="PCM_16")
    return b64encode(buf.getvalue()).decode("ascii")


def _multipart_data(payload: dict[str, Any]) -> dict[str, str]:
    data: dict[str, str] = {}
    for key, value in payload.items():
        if key.endswith("_base64") or value is None:
            continue
        if isinstance(value, (dict, list)):
            data[key] = json.dumps(value)
        else:
            data[key] = str(value)
    return data


def _multipart_audio_files(request: GenerationRequest) -> dict[str, tuple[str, bytes, str]]:
    files: dict[str, tuple[str, bytes, str]] = {}
    if request.source_audio is not None:
        wav = b64decode(_audio_to_base64_wav(request.source_audio, request.source_sample_rate))
        files["init_audio"] = ("init_audio.wav", wav, "audio/wav")
        files["inpaint_audio"] = ("inpaint_audio.wav", wav, "audio/wav")
        return files

    source_audio_path = request.source_audio_path or request.parameters.get("init_audio_path")
    if source_audio_path:
        path = Path(str(source_audio_path)).expanduser()
        if not path.exists():
            raise FileNotFoundError(path)
        content = path.read_bytes()
        files["init_audio"] = (path.name, content, "audio/wav")
        files["inpaint_audio"] = (path.name, content, "audio/wav")
    return files


def _local_or_allowlisted_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    return is_url_allowed(url)


def _parse_audio_response(response, *, allow_local_paths: bool, base_url: str = "") -> tuple[np.ndarray, int]:
    content_type = response.headers.get("content-type", "").lower()
    if "audio" in content_type or response.content.startswith(b"RIFF"):
        return _decode_audio_bytes(response.content)

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError("Stable Audio returned a non-audio response") from exc

    return _parse_audio_payload(data, allow_local_paths=allow_local_paths, base_url=base_url)


def _parse_audio_payload(data: Any, *, allow_local_paths: bool, base_url: str = "") -> tuple[np.ndarray, int]:
    if isinstance(data, list) and data:
        return _parse_audio_payload(data[0], allow_local_paths=allow_local_paths, base_url=base_url)
    if not isinstance(data, dict):
        raise RuntimeError("Stable Audio returned no audio")

    variations = data.get("variations") or data.get("outputs")
    if isinstance(variations, list) and variations:
        return _parse_audio_payload(variations[0], allow_local_paths=allow_local_paths, base_url=base_url)

    audio_files = data.get("audio_files")
    if isinstance(audio_files, list) and audio_files:
        return _decode_result_file(audio_files[0], allow_local_paths=allow_local_paths, base_url=base_url)

    audio_b64 = data.get("audio") or data.get("audio_base64") or data.get("base64")
    if isinstance(audio_b64, str) and audio_b64:
        if audio_b64.startswith("data:"):
            audio_b64 = audio_b64.split(",", 1)[-1]
        return _decode_audio_bytes(b64decode(audio_b64))

    artifacts = data.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        return _parse_audio_payload(artifacts[0], allow_local_paths=allow_local_paths, base_url=base_url)

    path = data.get("audio_path") or data.get("path")
    if allow_local_paths and isinstance(path, str) and path:
        return _decode_audio_file(path)

    url = data.get("url") or (data.get("audio_file") or {}).get("url")
    if isinstance(url, str) and url:
        return _download_audio_for_response(url, allow_local=allow_local_paths)

    status = str(data.get("status", "")).lower()
    if status == "error" and data.get("error"):
        raise RuntimeError(f"Stable Audio returned error: {redact_text(data.get('error'))}")
    if status in {"queued", "pending", "started", "in_progress"}:
        job_id = data.get("job_id", "")
        suffix = f": {job_id}" if job_id else ""
        raise RuntimeError(f"Stable Audio job did not return audio yet{suffix}")

    raise RuntimeError("Stable Audio returned no audio")


def _decode_result_file(file_ref: Any, *, allow_local_paths: bool, base_url: str = "") -> tuple[np.ndarray, int]:
    if not isinstance(file_ref, str) or not file_ref:
        raise RuntimeError("Stable Audio returned an empty audio file reference")
    path = Path(file_ref).expanduser()
    if allow_local_paths and path.is_absolute() and path.exists():
        return _decode_audio_file(file_ref)
    if allow_local_paths and base_url:
        encoded = quote(file_ref, safe="/")
        return _download_audio_for_response(f"{base_url}/files/{encoded}", allow_local=True)
    if allow_local_paths:
        return _decode_audio_file(file_ref)
    raise RuntimeError("Stable Audio returned a local audio file but local paths are disabled")


def _decode_audio_file(path: str) -> tuple[np.ndarray, int]:
    file_path = Path(path).expanduser()
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    return _decode_audio_bytes(file_path.read_bytes())


def _download_audio_for_response(url: str, *, allow_local: bool) -> tuple[np.ndarray, int]:
    import httpx

    if allow_local:
        allowed = _local_or_allowlisted_url(url)
    else:
        allowed = is_url_allowed(url)
    if not allowed:
        host = urlparse(url).hostname or "unknown"
        raise ValueError(f"Audio download blocked — host not in allowlist: {host}")

    with httpx.Client(timeout=60.0) as client:
        response = client.get(url)
        response.raise_for_status()
    return _decode_audio_bytes(response.content)


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

    if not is_url_allowed(url):
        host = urlparse(url).hostname or "unknown"
        raise ValueError(f"Audio download blocked — host not in allowlist: {host}")

    with httpx.Client(timeout=60.0) as client:
        response = client.get(url)
        response.raise_for_status()

    return _decode_audio_bytes(response.content)


# Backward-compatible name used by existing tests and imports.
StableAudioEngine = FalStableAudioEngine
