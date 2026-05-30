"""Stable Audio 3 mode routing and daemon endpoints."""

from __future__ import annotations

import numpy as np
from starlette.testclient import TestClient

from oram.config import OramConfig
from oram.engines.adapter import EngineSpec, GenerationRequest, GenerationResult
from oram.engines.capabilities import AudioCapability, EngineMode, EngineProvider
from oram.engines.registry import EngineRegistry
from oram.engines.router import EngineRouter
from oram.engines.stable_audio import (
    _build_stable_audio3_payload,
    _germinator_request_from_payload,
    _parse_audio_payload,
)
from oram_daemon.server import LocalOramService, create_app
from oram_library import OramLibrary
from oram_security.credentials import MemoryCredentialStore


class _FakeStableAudio3Engine:
    spec = EngineSpec(
        id="stable-audio-3-local",
        provider=EngineProvider.LOCAL,
        label="Fake Stable Audio 3",
        mode=EngineMode.LOCAL,
        capabilities=[
            AudioCapability.TEXT_TO_SOUND_EFFECT,
            AudioCapability.TEXT_TO_MUSIC,
            AudioCapability.AUDIO_TO_AUDIO,
            AudioCapability.AUDIO_INPAINTING,
            AudioCapability.AUDIO_CONTINUATION,
            AudioCapability.LORA_MIXING,
            AudioCapability.AUDIO_LATENT,
        ],
        requires_api_key=False,
        supports_seed=True,
        supports_audio_input=True,
        max_duration_seconds=380.0,
    )

    def __init__(self):
        self.requests: list[GenerationRequest] = []

    def is_available(self) -> bool:
        return True

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        sr = 48000
        samples = max(1, int(request.duration_seconds * sr))
        audio = np.zeros((samples, 2), dtype=np.float32)
        audio[:, 0] = 0.05
        audio[:, 1] = -0.05
        return GenerationResult(
            audio=audio,
            sample_rate=sr,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=request.prompt,
            duration_seconds=request.duration_seconds,
            metadata={"mode": request.parameters.get("stable_audio_mode")},
        )


def _service_with_fake_sa3(tmp_path):
    service = LocalOramService(
        OramConfig(mock_audio=True, session_dir=tmp_path / "sessions"),
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore(),
        mock_audio=True,
    )
    fake = _FakeStableAudio3Engine()
    registry = EngineRegistry()
    registry.register(fake)
    service.engine_registry = registry
    service.engine_router = EngineRouter(registry)
    service.router.engine_registry = registry
    service.router.engine_router = service.engine_router
    return service, fake


def test_stable_audio_morph_uses_source_layer_and_params(tmp_path):
    service, fake = _service_with_fake_sa3(tmp_path)
    service.layers.assign_buffer(
        service.layers.layers[0],
        np.ones((24000, 2), dtype=np.float32) * 0.1,
    )
    app = create_app(service, auth_token="")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/stable-audio/render",
            json={
                "prompt": "turn this loop into bowed glass",
                "mode": "morph",
                "duration": 0.5,
                "model": "stable-audio-3-local",
                "source_layer": 1,
                "noise_depth": 0.65,
                "seed": 1234,
                "variation_count": 3,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["layer"] == 2
    assert data["sound"]["tags"] == ["mode:morph", "stable-audio"]
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.source_audio is not None
    assert request.parameters["stable_audio_mode"] == "morph"
    assert request.parameters["init_noise_level"] == 0.65
    assert request.parameters["seed"] == 1234
    assert request.parameters["variation_count"] == 3


def test_stable_audio_inpaint_uses_loop_region_when_no_range_given(tmp_path):
    service, fake = _service_with_fake_sa3(tmp_path)
    layer = service.layers.layers[0]
    service.layers.assign_buffer(layer, np.zeros((48000, 2), dtype=np.float32))
    service.layers.set_loop_region(layer, start_sample=12000, end_sample=24000, enabled=True)
    app = create_app(service, auth_token="")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/stable-audio/render",
            json={
                "prompt": "replace with dry ceramic impacts",
                "mode": "inpaint",
                "duration": 1.0,
                "model": "stable-audio-3-local",
                "source_layer": 1,
            },
        )

    assert response.status_code == 200
    request = fake.requests[0]
    assert request.parameters["inpaint_ranges"] == [{"start": 0.25, "end": 0.5}]


def test_plugin_stable_audio_render_does_not_assign_daemon_layer(tmp_path):
    service, fake = _service_with_fake_sa3(tmp_path)
    app = create_app(service, auth_token="")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/plugin/stable-audio/render",
            json={
                "prompt": "short magnetic click texture",
                "mode": "lora_mixer",
                "duration": 0.5,
                "model": "stable-audio-3-local",
                "lora_a_path": "/tmp/a.safetensors",
                "lora_a_strength": 0.8,
            },
        )
        state = client.get("/state").json()

    assert response.status_code == 200
    assert response.json()["layer"] is None
    assert all(layer["state"] == "empty" for layer in state["layers"])
    assert fake.requests[0].parameters["lora_stack"][0]["path"] == "/tmp/a.safetensors"


def test_stable_audio_morph_requires_source_audio(tmp_path):
    service, _fake = _service_with_fake_sa3(tmp_path)
    app = create_app(service, auth_token="")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/stable-audio/render",
            json={
                "prompt": "turn this into a soft drone",
                "mode": "morph",
                "duration": 0.5,
                "model": "stable-audio-3-local",
                "source_layer": 1,
            },
        )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_stable_audio3_engines_register_from_config():
    cfg = OramConfig()
    cfg.stability_api_key = "test-stability-key"
    cfg.stable_audio_service_url = "http://127.0.0.1:19999"

    registry = EngineRegistry.from_config(cfg)

    assert registry.get("stability-stable-audio-3") is not None
    assert registry.get("stable-audio-3-local") is not None


def test_stable_audio_local_runtime_fields_are_forwarded(tmp_path):
    service, fake = _service_with_fake_sa3(tmp_path)
    app = create_app(service, auth_token="")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/stable-audio/render",
            json={
                "prompt": "short ceramic tick",
                "mode": "generate",
                "duration": 0.5,
                "provider": "local",
                "model": "stable-audio-3-local",
                "local_provider": "stable_audio_python",
                "local_model": "small-sfx",
                "service_url": "http://127.0.0.1:8765",
                "chunked_decode": False,
            },
        )

    assert response.status_code == 200
    request = fake.requests[0]
    assert request.parameters["local_provider"] == "stable_audio_python"
    assert request.parameters["local_model"] == "small-sfx"
    assert request.parameters["service_url"] == "http://127.0.0.1:8765"
    assert request.parameters["chunked_decode"] is False


def test_local_sa3_payload_maps_to_germinator_audio_to_audio():
    import soundfile as sf

    source = np.zeros((24000, 2), dtype=np.float32)
    request = GenerationRequest(
        prompt="turn this into metallic dust",
        duration_seconds=0.5,
        source_audio=source,
        source_sample_rate=48000,
        parameters={
            "stable_audio_mode": "morph",
            "init_noise_level": 0.42,
            "variation_count": 2,
        },
    )
    payload = _build_stable_audio3_payload(
        request,
        provider_backend="local_mlx",
        model="small-music",
        decoder="same-s",
        max_duration=380.0,
    )

    endpoint, body, temp_paths = _germinator_request_from_payload(payload)
    try:
        assert endpoint == "/audio-to-audio"
        assert body["provider"] == "stable_audio_mlx"
        assert body["model"] == "sm-music"
        assert body["init_noise_level"] == 0.42
        assert body["batch_size"] == 2
        assert body["input_audio_path"]
        assert sf.info(body["input_audio_path"]).samplerate == 44100
    finally:
        for path in temp_paths:
            path.unlink(missing_ok=True)


def test_local_sa3_germinator_input_path_is_resampled(tmp_path):
    import soundfile as sf

    source_path = tmp_path / "source_48k.wav"
    sf.write(source_path, np.zeros((48000, 1), dtype=np.float32), 48000)

    endpoint, body, temp_paths = _germinator_request_from_payload({
        "mode": "morph",
        "provider": "stable_audio_mlx",
        "model": "sm-sfx",
        "prompt": "turn this into bright dust",
        "init_audio_path": str(source_path),
        "init_noise_level": 0.5,
    })
    try:
        info = sf.info(body["input_audio_path"])
        assert endpoint == "/audio-to-audio"
        assert info.samplerate == 44100
        assert info.channels == 2
        assert body["input_audio_path"] != str(source_path)
    finally:
        for path in temp_paths:
            path.unlink(missing_ok=True)


def test_parse_audio_files_result_from_local_sidecar(tmp_path):
    import soundfile as sf

    path = tmp_path / "sidecar.wav"
    sf.write(path, np.zeros((1024, 2), dtype=np.float32), 48000)

    audio, sample_rate = _parse_audio_payload(
        {"status": "done", "audio_files": [str(path)]},
        allow_local_paths=True,
    )

    assert sample_rate == 48000
    assert audio.shape == (1024, 2)
