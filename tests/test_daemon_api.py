"""tests for the local daemon API."""

from __future__ import annotations

from io import BytesIO

import numpy as np
from starlette.testclient import TestClient

from oram.config import OramConfig
from oram_daemon.server import LocalOramService, create_app
from oram_library import OramLibrary
from oram_security.credentials import MemoryCredentialStore


def _wav_bytes(sample_rate: int = 48000) -> bytes:
    import soundfile as sf

    buf = BytesIO()
    audio = np.zeros((sample_rate // 20, 2), dtype=np.float32)
    sf.write(buf, audio, sample_rate, format="WAV")
    return buf.getvalue()


def test_daemon_health_state_and_generate_no_secrets(tmp_path):
    secret = "unit-test-provider-key"
    cfg = OramConfig(mock_audio=True, session_dir=tmp_path / "sessions")
    service = LocalOramService(
        cfg,
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore({"elevenlabs": secret}),
        mock_audio=True,
    )
    app = create_app(service, auth_token="")
    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        state = client.get("/state").json()
        assert secret not in str(state)
        assert "api_key" not in str(state).lower()

        status = client.get("/credentials/status").json()
        assert status["elevenlabs"]["configured"] is True
        assert secret not in str(status)

        generated = client.post(
            "/generate",
            json={"prompt": "distant room tone", "duration": 0.5, "model": "local-mock"},
        )
        assert generated.status_code == 200
        data = generated.json()
        assert data["status"] == "ok"
        assert data["sound"]["id"].startswith("oram_sound_")

        sounds = client.get("/library/sounds").json()
        assert len(sounds["sounds"]) == 1


def test_daemon_auth_blocks_mutations(tmp_path):
    service = LocalOramService(
        OramConfig(mock_audio=True, session_dir=tmp_path / "sessions"),
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore(),
        mock_audio=True,
    )
    app = create_app(service, auth_token="daemon-token")
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200
        blocked = client.post("/command", json={"text": "select layer 1"})
        assert blocked.status_code == 401
        allowed = client.post(
            "/command",
            json={"text": "select layer 1"},
            headers={"Authorization": "Bearer daemon-token"},
        )
        assert allowed.status_code == 200


def test_daemon_dashboard_control_endpoints(tmp_path):
    cfg = OramConfig(mock_audio=True, session_dir=tmp_path / "sessions")
    service = LocalOramService(
        cfg,
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore(),
        mock_audio=True,
    )
    app = create_app(service, auth_token="")
    with TestClient(app, raise_server_exceptions=False) as client:
        generated = client.post(
            "/generate",
            json={"prompt": "quiet machine", "duration": 0.5, "model": "local-mock"},
        )
        assert generated.status_code == 200

        state = client.get("/state").json()
        assert state["input_mode"] == "prompt"
        assert "input_level" in state
        assert state["layers"][0]["waveform_revision"] >= 1

        waveform = client.get("/waveform/1?points=80")
        assert waveform.status_code == 200
        waveform_data = waveform.json()
        assert waveform_data["points"] == 80
        assert waveform_data["revision"] == state["layers"][0]["waveform_revision"]
        assert "peaks" in waveform_data

        looped = client.post("/layer/loop-region", json={"target": 1, "start_pct": 10, "end_pct": 80})
        assert looped.status_code == 200
        loop_data = looped.json()
        assert loop_data["status"] == "ok"
        assert loop_data["loop_enabled"] is True

        volume = client.post("/layer/volume", json={"target": 1, "volume": 0.5})
        assert volume.status_code == 200
        assert client.get("/state").json()["layers"][0]["volume"] == 0.5

        exported = client.post("/layer/export", json={"target": 1})
        assert exported.status_code == 200
        assert exported.json()["status"] == "ok"

        mode = client.post("/input-mode", json={"mode": "listen"})
        assert mode.status_code == 200
        mode_state = client.get("/state").json()
        assert mode_state["auto_listen"] is True
        assert mode_state["input_mode"] == "prompt"

        killed = client.post("/kill", json={})
        assert killed.status_code == 200
        assert client.get("/state").json()["layers"][0]["muted"] is True

        cleared = client.post("/layer/clear", json={"target": 1})
        assert cleared.status_code == 200
        assert client.get("/state").json()["layers"][0]["state"] == "empty"


def test_daemon_settings_restart_audio_and_accept_default_devices(tmp_path):
    cfg = OramConfig(mock_audio=True, session_dir=tmp_path / "sessions")
    service = LocalOramService(
        cfg,
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore(),
        mock_audio=True,
    )
    app = create_app(service, auth_token="")
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/settings",
            json={"input_device": -1, "output_device": -1, "block_size": 256},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "audio engine restarted" in data["changes"]
        assert client.get("/state").json()["block_size"] == 256
        devices = client.get("/devices").json()
        assert devices["current_input"] is None
        assert devices["current_output"] is None


def test_daemon_upload_layer_imports_audio(tmp_path):
    cfg = OramConfig(mock_audio=True, session_dir=tmp_path / "sessions")
    service = LocalOramService(
        cfg,
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore(),
        mock_audio=True,
    )
    app = create_app(service, auth_token="")
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/layer/upload?target=3&filename=field_recording.wav",
            content=_wav_bytes(),
            headers={"Content-Type": "audio/wav"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["layer"] == 3

        state = client.get("/state").json()
        layer = state["layers"][2]
        assert layer["state"] == "active"
        assert layer["source_type"] == "imported"
        assert layer["duration"] > 0


def test_plugin_parse_does_not_mutate_daemon_state(tmp_path):
    service = LocalOramService(
        OramConfig(mock_audio=True, session_dir=tmp_path / "sessions"),
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore(),
        mock_audio=True,
    )
    app = create_app(service, auth_token="")
    with TestClient(app, raise_server_exceptions=False) as client:
        before = client.get("/state").json()
        parsed = client.post("/actions/parse", json={"text": "select layer 3"})
        after = client.get("/state").json()

        assert parsed.status_code == 200
        assert parsed.json()["action"] == {"action": "select_layer", "target": 3}
        assert before["selected_layer"] == after["selected_layer"] == 1


def test_plugin_generate_writes_library_without_assigning_layer(tmp_path):
    service = LocalOramService(
        OramConfig(mock_audio=True, session_dir=tmp_path / "sessions"),
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore(),
        mock_audio=True,
    )
    app = create_app(service, auth_token="")
    with TestClient(app, raise_server_exceptions=False) as client:
        generated = client.post(
            "/plugin/generate",
            json={"prompt": "quiet room tone", "duration": 0.5, "model": "local-mock"},
        )
        assert generated.status_code == 200
        data = generated.json()
        assert data["status"] == "ok"
        assert data["sound"]["id"].startswith("oram_sound_")

        state = client.get("/state").json()
        assert all(layer["state"] == "empty" for layer in state["layers"])

        sounds = client.get("/library/sounds").json()
        assert [sound["id"] for sound in sounds["sounds"]] == [data["sound"]["id"]]


def test_daemon_refreshes_stability_engine_from_credential_store(tmp_path):
    service = LocalOramService(
        OramConfig(mock_audio=True, session_dir=tmp_path / "sessions"),
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore({"stability": "unit-test-stability-key"}),
        mock_audio=True,
    )
    app = create_app(service, auth_token="")
    with TestClient(app, raise_server_exceptions=False) as client:
        providers = client.get("/providers").json()
        ids = {engine["id"] for engine in providers["engines"]}
        assert "stability-stable-audio-25" in ids
        status = client.get("/credentials/status").json()
        assert status["stability"]["configured"] is True
        assert "unit-test-stability-key" not in str(providers)


def test_daemon_websocket_streams_state(tmp_path):
    service = LocalOramService(
        OramConfig(mock_audio=True, session_dir=tmp_path / "sessions"),
        library=OramLibrary(tmp_path / "library"),
        credential_store=MemoryCredentialStore(),
        mock_audio=True,
    )
    app = create_app(service, auth_token="")
    with TestClient(app, raise_server_exceptions=False) as client:
        with client.websocket_connect("/ws") as ws:
            state = ws.receive_json()
            assert state["version"]
            assert "layers" in state
