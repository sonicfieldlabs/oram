"""tests for the local daemon API."""

from __future__ import annotations

from starlette.testclient import TestClient

from oram.config import OramConfig
from oram_daemon.server import LocalOramService, create_app
from oram_library import OramLibrary
from oram_security.credentials import MemoryCredentialStore


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
