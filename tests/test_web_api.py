"""tests for web API endpoints — typed routes, auth, state snapshot."""

from __future__ import annotations

import os
import time
from io import BytesIO

import numpy as np
import pytest

os.environ.pop("ORAM_DASHBOARD_TOKEN", None)


def _wav_bytes(sample_rate: int = 48000) -> bytes:
    import soundfile as sf

    buf = BytesIO()
    audio = np.zeros((sample_rate // 20, 2), dtype=np.float32)
    sf.write(buf, audio, sample_rate, format="WAV")
    return buf.getvalue()


@pytest.fixture(scope="module")
def client():
    """create a test client with mock audio."""
    from starlette.testclient import TestClient

    import oram.web.server as srv
    srv._DASHBOARD_TOKEN = ""
    srv.app.state.mock_audio = True
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        yield c


class TestStateEndpoint:
    """GET /api/state returns safe snapshot."""

    def test_state_returns_layers(self, client):
        resp = client.get("/api/state")
        assert resp.status_code == 200
        data = resp.json()
        # may not be initialized in test context
        if "error" not in data:
            assert "layers" in data
            assert "version" in data

    def test_state_no_secrets(self, client):
        resp = client.get("/api/state")
        data = resp.json()
        text = str(data)
        assert "ELEVENLABS" not in text
        assert "api_key" not in text.lower()


class TestCommandEndpoint:
    """POST /api/command routes text commands."""

    def test_command_endpoint_exists(self, client):
        resp = client.post("/api/command", json={"text": "select layer 1"})
        assert resp.status_code == 200

    def test_invalid_command_returns_unknown_not_500(self, client):
        resp = client.post("/api/command", json={"text": "select layer 9"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"]["action"] == "unknown"

    def test_volume_command_preserves_quiet_and_mute(self, client):
        quiet = client.post("/api/command", json={"text": "set volume layer 1 0.009"})
        assert quiet.status_code == 200
        assert client.get("/api/state").json()["layers"][0]["volume"] == 0.009

        mute = client.post("/api/command", json={"text": "set volume layer 1 0"})
        assert mute.status_code == 200
        assert client.get("/api/state").json()["layers"][0]["volume"] == 0.0


class TestClearLayerEndpoint:
    """POST /api/clear-layer with typed request."""

    def test_clear_empty_layer(self, client):
        resp = client.post("/api/clear-layer", json={"target": 1})
        assert resp.status_code == 200


class TestExportLayerEndpoint:
    """POST /api/export-layer."""

    def test_export_empty_layer(self, client):
        resp = client.post("/api/export-layer", json={"target": 1})
        assert resp.status_code == 200
        data = resp.json()
        # may be "not initialized" or "empty" depending on test context
        if "error" in data:
            assert data["error"] in ("not initialized", "empty")


class TestKillEndpoint:
    """POST /api/kill."""

    def test_kill_returns_ok(self, client):
        resp = client.post("/api/kill", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"


class TestAutoListenToggle:
    """POST /api/auto-listen."""

    def test_toggle_auto_listen(self, client):
        resp = client.post("/api/auto-listen")
        assert resp.status_code == 200


class TestDevicesEndpoint:
    """GET /api/devices."""

    def test_devices_returns_list(self, client):
        resp = client.get("/api/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert "devices" in data


class TestUploadLayerEndpoint:
    """POST /api/upload-layer imports user audio into a layer."""

    def test_upload_wav_to_layer(self, client):
        import oram.web.server as srv

        resp = client.post(
            "/api/upload-layer?target=2&filename=user_loop.wav",
            content=_wav_bytes(),
            headers={"Content-Type": "audio/wav"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["layer"] == 2

        state = client.get("/api/state").json()
        layer = state["layers"][1]
        assert layer["state"] == "active"
        assert layer["source_type"] == "imported"
        assert layer["duration"] > 0

        assert srv._layer_manager is not None
        srv._layer_manager.clear(srv._layer_manager.layers[1])


class TestTokenAuthOnPost:
    """token auth blocks POST when token is set."""

    def test_post_blocked_without_token(self):
        from starlette.testclient import TestClient

        import oram.web.server as srv
        srv._DASHBOARD_TOKEN = "secret123"
        c = TestClient(srv.app, raise_server_exceptions=False)
        resp = c.post("/api/kill", json={})
        assert resp.status_code == 401
        srv._DASHBOARD_TOKEN = ""

    def test_post_allowed_with_token(self):
        from starlette.testclient import TestClient

        import oram.web.server as srv
        srv._DASHBOARD_TOKEN = "secret123"
        c = TestClient(srv.app, raise_server_exceptions=False)
        resp = c.post(
            "/api/kill",
            json={},
            headers={"Authorization": "Bearer secret123"},
        )
        assert resp.status_code == 200
        srv._DASHBOARD_TOKEN = ""


class TestLoopRegionEndpoint:
    """POST /api/loop-region."""

    def test_loop_region_empty_layer(self, client):
        resp = client.post("/api/loop-region", json={"target": 1, "start_pct": 10, "end_pct": 90})
        assert resp.status_code == 400
        data = resp.json()
        assert data.get("status") == "error"
        assert "empty" in data.get("message", "").lower()

    def test_loop_region_endpoint_exists(self, client):
        resp = client.post("/api/loop-region", json={"target": 1, "enabled": False})
        assert resp.status_code in (200, 400)

    def test_loop_region_no_secrets(self, client):
        resp = client.post("/api/loop-region", json={"target": 1})
        data = resp.json()
        text = str(data)
        assert "ELEVENLABS" not in text
        assert "api_key" not in text.lower()

    def test_loop_region_reversed_range_is_error(self, client):
        import numpy as np

        import oram.web.server as srv

        assert srv._layer_manager is not None
        layer = srv._layer_manager.layers[0]
        srv._layer_manager.assign_buffer(layer, np.zeros((48000, 2), dtype=np.float32))
        resp = client.post("/api/loop-region", json={"target": 1, "start_pct": 90, "end_pct": 10})
        data = resp.json()
        assert resp.status_code == 400
        assert data.get("status") == "error"
        assert "invalid loop region" in data.get("message", "")
        srv._layer_manager.clear(layer)


class TestLoopFadesEndpoint:
    """POST /api/loop-fades."""

    def test_loop_fades_set_and_clear(self, client):
        import oram.web.server as srv

        assert srv._layer_manager is not None
        layer = srv._layer_manager.layers[0]
        srv._layer_manager.assign_buffer(layer, np.zeros((48000, 2), dtype=np.float32))
        srv._layer_manager.set_loop_region(layer, 12000, 36000)

        resp = client.post(
            "/api/loop-fades",
            json={"target": 1, "fade_in_pct": 10, "fade_out_pct": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        state = client.get("/api/state").json()
        assert state["layers"][0]["loop_fade_in_seconds"] == 0.1
        assert state["layers"][0]["loop_fade_out_seconds"] == 0.05

        client.post("/api/loop-region", json={"target": 1, "enabled": False})
        state = client.get("/api/state").json()
        assert state["layers"][0]["loop_fade_in_seconds"] == 0.0
        assert state["layers"][0]["loop_fade_out_seconds"] == 0.0
        srv._layer_manager.clear(layer)


class TestInpaintAndReverseEndpoints:
    """POST /api/inpaint-regions and /api/playback-reverse."""

    def test_inpaint_regions_and_reverse_state(self, client):
        import oram.web.server as srv

        assert srv._layer_manager is not None
        layer = srv._layer_manager.layers[0]
        srv._layer_manager.assign_buffer(layer, np.zeros((48000, 2), dtype=np.float32))

        inpaint = client.post(
            "/api/inpaint-regions",
            json={"target": 1, "regions": [{"start_pct": 25, "end_pct": 50}]},
        )
        assert inpaint.status_code == 200
        assert inpaint.json()["status"] == "ok"

        reverse = client.post("/api/playback-reverse", json={"target": 1, "enabled": True})
        assert reverse.status_code == 200
        assert reverse.json()["enabled"] is True

        state = client.get("/api/state").json()
        layer_state = state["layers"][0]
        assert layer_state["inpaint_regions"][0]["start_seconds"] == 0.25
        assert layer_state["inpaint_regions"][0]["end_seconds"] == 0.5
        assert layer_state["playback_reverse"] is True
        srv._layer_manager.clear(layer)


class TestUndoRedoAndResetEndpoints:
    """POST /api/undo, /api/redo, and stronger /api/kill reset."""

    def test_undo_redo_reverse_playback(self, client):
        import oram.web.server as srv

        assert srv._layer_manager is not None
        srv._reset_to_initial_audio_state()
        srv._undo_stack.clear()
        srv._redo_stack.clear()
        layer = srv._layer_manager.layers[0]
        srv._layer_manager.assign_buffer(layer, np.zeros((48000, 2), dtype=np.float32))

        reverse = client.post("/api/playback-reverse", json={"target": 1, "enabled": True})
        assert reverse.status_code == 200
        assert client.get("/api/state").json()["layers"][0]["playback_reverse"] is True
        assert client.get("/api/state").json()["can_undo"] is True

        undo = client.post("/api/undo")
        assert undo.status_code == 200
        assert undo.json()["status"] == "ok"
        assert client.get("/api/state").json()["layers"][0]["playback_reverse"] is False

        redo = client.post("/api/redo")
        assert redo.status_code == 200
        assert redo.json()["status"] == "ok"
        assert client.get("/api/state").json()["layers"][0]["playback_reverse"] is True
        srv._reset_to_initial_audio_state()
        srv._undo_stack.clear()
        srv._redo_stack.clear()

    def test_kill_resets_audio_state_and_is_undoable(self, client):
        import oram.web.server as srv

        assert srv._layer_manager is not None
        srv._reset_to_initial_audio_state()
        srv._undo_stack.clear()
        srv._redo_stack.clear()
        layer = srv._layer_manager.layers[0]
        srv._layer_manager.assign_buffer(layer, np.ones((48000, 2), dtype=np.float32))
        srv._layer_manager.set_loop_region(layer, 12000, 36000)
        srv._layer_manager.set_loop_fades(layer, 2400, 2400)
        layer.volume = 0.5
        srv._layer_manager.mute(layer)
        srv._layer_manager.selected = 2

        reset = client.post("/api/kill", json={})
        assert reset.status_code == 200
        state = client.get("/api/state").json()
        assert state["layers"][0]["state"] == "empty"
        assert state["layers"][0]["duration"] == 0
        assert state["layers"][0]["loop_enabled"] is False
        assert state["layers"][0]["volume"] == 1.0
        assert state["layers"][0]["muted"] is False
        assert state["selected_layer"] == 0
        assert state["can_undo"] is True

        undo = client.post("/api/undo")
        assert undo.status_code == 200
        restored = client.get("/api/state").json()
        assert restored["layers"][0]["state"] == "muted"
        assert restored["layers"][0]["duration"] == 1.0
        assert restored["layers"][0]["loop_enabled"] is True
        assert restored["layers"][0]["loop_fade_in_seconds"] == 0.05
        assert restored["layers"][0]["volume"] == 0.5
        assert restored["selected_layer"] == 2


class TestMasterRecordEndpoint:
    """POST /api/master-record."""

    def test_master_record_start_stop_exports_file(self, client):
        resp = client.post("/api/master-record", json={"action": "start"})
        assert resp.status_code == 200
        assert resp.json()["recording"] is True

        time.sleep(0.05)

        stop = client.post("/api/master-record", json={"action": "stop"})
        assert stop.status_code == 200
        data = stop.json()
        assert data["recording"] is False
        assert data["samples"] > 0
        assert os.path.exists(data["path"])


class TestWaveformEndpoint:
    """GET /api/waveform/{target}."""

    def test_waveform_empty_layer(self, client):
        resp = client.get("/api/waveform/1")
        assert resp.status_code == 200
        data = resp.json()
        if "error" not in data:
            assert "peaks" in data
            assert "rms" in data
            assert data["points"] >= 64

    def test_waveform_invalid_layer(self, client):
        resp = client.get("/api/waveform/99")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_waveform_points_clamped(self, client):
        resp = client.get("/api/waveform/1?points=10")
        assert resp.status_code == 200
        data = resp.json()
        if "error" not in data:
            assert data["points"] >= 64

    def test_waveform_no_secrets(self, client):
        resp = client.get("/api/waveform/1")
        data = resp.json()
        text = str(data)
        assert "ELEVENLABS" not in text
        assert "api_key" not in text.lower()
