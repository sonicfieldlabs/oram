"""tests for dashboard security — token auth, origin checks, secret scrubbing."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def _no_token(monkeypatch):
    """ensure no token is set."""
    monkeypatch.delenv("ORAM_DASHBOARD_TOKEN", raising=False)


@pytest.fixture
def _with_token(monkeypatch):
    """set a known token."""
    monkeypatch.setenv("ORAM_DASHBOARD_TOKEN", "test-secret-42")


def _make_client():
    """reimport server module to pick up current env."""
    # force reimport to pick up token change
    import oram.web.server as srv
    srv._DASHBOARD_TOKEN = os.environ.get("ORAM_DASHBOARD_TOKEN", "")
    return TestClient(srv.app, raise_server_exceptions=False)


class TestTokenAuth:
    """mutation endpoints should require token when ORAM_DASHBOARD_TOKEN is set."""

    def test_state_always_open(self, _with_token):
        client = _make_client()
        resp = client.get("/api/state")
        # GET endpoints are always open
        assert resp.status_code == 200

    def test_mutation_rejected_without_token(self, _with_token):
        client = _make_client()
        resp = client.post("/api/auto-listen")
        assert resp.status_code == 401
        assert "unauthorized" in resp.json().get("error", "")

    def test_mutation_accepted_with_token(self, _with_token):
        client = _make_client()
        resp = client.post(
            "/api/auto-listen",
            headers={"Authorization": "Bearer test-secret-42"},
        )
        assert resp.status_code == 200

    def test_mutation_open_when_no_token(self, _no_token):
        client = _make_client()
        resp = client.post("/api/auto-listen")
        assert resp.status_code == 200


class TestStateSecrets:
    """api/state must never contain API keys or tokens."""

    def test_no_api_key_in_state(self, _no_token):
        import oram.web.server as srv

        client = _make_client()
        resp = client.get("/api/state")
        data = resp.json()
        text = str(data)
        configured_key = srv._config.elevenlabs_api_key if srv._config else ""
        if configured_key:
            assert configured_key not in text
        assert "api_key" not in text
        assert "token" not in text.lower() or "dashboard_token" not in text.lower()


class TestWebSocketOrigin:
    """websocket should reject unknown origins."""

    def test_localhost_origin_allowed(self, _no_token):
        client = _make_client()
        with client.websocket_connect(
            "/ws", headers={"origin": "http://localhost:3333"}
        ) as ws:
            data = ws.receive_json()
            assert "version" in data or "error" in data

    def test_unknown_origin_rejected(self, _no_token):
        client = _make_client()
        try:
            with client.websocket_connect(
                "/ws", headers={"origin": "http://evil.example.com"}
            ) as ws:
                # should be closed by server
                ws.receive_json()
                pytest.fail("should have been rejected")
        except Exception:
            pass  # connection refused or closed — expected

    def test_ws_token_required(self, _with_token):
        client = _make_client()
        try:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                pytest.fail("should have been rejected without token")
        except Exception:
            pass

    def test_ws_token_accepted(self, _with_token):
        client = _make_client()
        with client.websocket_connect("/ws?token=test-secret-42") as ws:
            data = ws.receive_json()
            assert "version" in data or "error" in data
