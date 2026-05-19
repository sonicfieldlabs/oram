"""tests for ElevenLabs gateway adapters — mocked HTTP, cost tracking, response parsing."""

from __future__ import annotations

import numpy as np

from oram.gateway.client import ElevenLabsHTTPClient


class TestElevenLabsHTTPClient:
    """shared client behavior."""

    def test_headers_contain_api_key(self):
        client = ElevenLabsHTTPClient(api_key="test_key_123")
        headers = client.headers
        assert headers["xi-api-key"] == "test_key_123"
        assert "Content-Type" in headers

    def test_parse_cost_header(self):
        """cost header extraction."""
        class FakeResp:
            headers = {"character-cost": "42.5"}
        assert ElevenLabsHTTPClient.parse_cost_header(FakeResp()) == 42.5

    def test_parse_cost_header_missing(self):
        class FakeResp:
            headers = {}
        assert ElevenLabsHTTPClient.parse_cost_header(FakeResp()) is None

    def test_format_error_json(self):
        class FakeResp:
            status_code = 400
            def json(self):
                return {"detail": {"message": "bad request"}}
        assert "bad request" in ElevenLabsHTTPClient.format_error(FakeResp())

    def test_format_error_non_json(self):
        class FakeResp:
            status_code = 500
            def json(self):
                raise ValueError
        result = ElevenLabsHTTPClient.format_error(FakeResp())
        assert "500" in result


class TestSFXAdapter:
    """SFX gateway tests."""

    def test_cost_estimation(self):
        from oram.gateway.sfx import SFXAdapter
        adapter = SFXAdapter.__new__(SFXAdapter)
        # 10 seconds at 40 credits/sec = 400
        cost = adapter._estimate_cost(10.0)
        assert cost == 400.0


class TestMusicEndpoint:
    """music gateway endpoint correctness."""

    def test_music_url_not_generate(self):
        import inspect

        from oram.gateway import music
        source = inspect.getsource(music.MusicAdapter.generate)
        # should use /music not /music/generate
        assert "/music\"" in source or "/music," in source
        assert "/music/generate" not in source


class TestScribeModel:
    """scribe gateway model correctness."""

    def test_scribe_v2_model(self):
        import inspect

        from oram.gateway import scribe
        source = inspect.getsource(scribe.ScribeAdapter.transcribe)
        assert "scribe_v2" in source
        assert "scribe_v1" not in source


class TestResampleUtility:
    """audio resample and normalization."""

    def test_mono_to_stereo(self):
        from oram.audio.resample import ensure_stereo_float32
        mono = np.random.randn(48000).astype(np.float32) * 0.5
        stereo = ensure_stereo_float32(mono, 48000, 48000)
        assert stereo.ndim == 2
        assert stereo.shape[1] == 2
        assert np.allclose(stereo[:, 0], stereo[:, 1])

    def test_resample_44100_to_48000(self):
        from oram.audio.resample import ensure_stereo_float32
        audio = np.random.randn(44100, 2).astype(np.float32) * 0.5
        resampled = ensure_stereo_float32(audio, 44100, 48000)
        expected_length = int(44100 * (48000 / 44100))
        assert abs(resampled.shape[0] - expected_length) <= 1

    def test_normalization_hot_signal(self):
        from oram.audio.resample import ensure_stereo_float32
        hot = np.ones((4800, 2), dtype=np.float32) * 2.0
        normalized = ensure_stereo_float32(hot, 48000, 48000)
        assert np.max(np.abs(normalized)) <= 0.91

    def test_same_rate_passthrough(self):
        from oram.audio.resample import ensure_stereo_float32
        audio = np.random.randn(4800, 2).astype(np.float32) * 0.5
        result = ensure_stereo_float32(audio, 48000, 48000)
        assert result.shape == audio.shape

    def test_empty_audio_returns_empty_stereo(self):
        from oram.audio.resample import ensure_stereo_float32
        result = ensure_stereo_float32(np.zeros((0,), dtype=np.float32), 44100, 48000)
        assert result.shape == (0, 2)


class TestGatewayIntegration:
    """router-level gateway normalization."""

    def test_router_resamples_gateway_audio_to_session_rate(self):
        from oram.audio.engine import MockAudioEngine
        from oram.audio.layer import LayerManager
        from oram.command.router import ActionRouter
        from oram.gateway.base import EngineResult
        from oram.types import OramSession

        class FakeAdapter:
            def generate(self, prompt, params):
                return EngineResult(
                    audio=np.ones((4410, 1), dtype=np.float32) * 0.1,
                    sample_rate=44100,
                    engine="sfx",
                    prompt_used=prompt,
                )

        session = OramSession(id="test", scene="test", sample_rate=48000)
        layers = LayerManager(sample_rate=48000)
        engine = MockAudioEngine(session, layers, sample_rate=48000)
        router = ActionRouter(session, layers, engine, gateway={"sfx": FakeAdapter()})

        audio = router._call_engine("sfx", "tone", 0.1)

        assert audio is not None
        assert audio.ndim == 2
        assert audio.shape[1] == 2
        assert abs(audio.shape[0] - 4800) <= 1
