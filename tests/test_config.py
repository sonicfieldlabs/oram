"""tests for OramConfig — env parsing, defaults, duration validation."""

from __future__ import annotations

from oram.config import OramConfig, _bool_env


class TestDefaults:
    """safe defaults match documentation."""

    def test_generator_default_is_mock(self):
        cfg = OramConfig()
        assert cfg.generator_backend == "mock"

    def test_llm_default_is_none(self):
        cfg = OramConfig()
        assert cfg.llm_backend == "none"

    def test_auto_listen_default_off(self):
        cfg = OramConfig()
        assert cfg.auto_listen is False

    def test_sample_rate_default(self):
        cfg = OramConfig()
        assert cfg.sample_rate == 48000


class TestEnvParsing:
    """env vars are parsed correctly."""

    def test_from_env_sample_rate(self, monkeypatch):
        monkeypatch.setenv("ORAM_SAMPLE_RATE", "44100")
        cfg = OramConfig.from_env()
        assert cfg.sample_rate == 44100

    def test_from_env_generator_backend(self, monkeypatch):
        monkeypatch.setenv("ORAM_GENERATOR_BACKEND", "elevenlabs")
        cfg = OramConfig.from_env()
        assert cfg.generator_backend == "elevenlabs"

    def test_from_env_auto_listen_true(self, monkeypatch):
        monkeypatch.setenv("ORAM_AUTO_LISTEN", "true")
        cfg = OramConfig.from_env()
        assert cfg.auto_listen is True

    def test_from_env_auto_listen_false(self, monkeypatch):
        monkeypatch.setenv("ORAM_AUTO_LISTEN", "false")
        cfg = OramConfig.from_env()
        assert cfg.auto_listen is False

    def test_from_env_listening_route(self, monkeypatch):
        monkeypatch.setenv("ORAM_DEFAULT_LISTENING_ROUTE", "technical")
        cfg = OramConfig.from_env()
        assert cfg.default_listening_route == "technical"

    def test_from_env_default_engine(self, monkeypatch):
        monkeypatch.setenv("ORAM_DEFAULT_ENGINE", "sfx")
        cfg = OramConfig.from_env()
        assert cfg.default_engine == "sfx"

    def test_from_env_dashboard_token(self, monkeypatch):
        monkeypatch.setenv("ORAM_DASHBOARD_TOKEN", "secret123")
        cfg = OramConfig.from_env()
        assert cfg.dashboard_token == "secret123"

    def test_from_env_api_key(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "unit-test-provider-key")
        cfg = OramConfig.from_env()
        assert cfg.elevenlabs_api_key == "unit-test-provider-key"


class TestBoolEnv:
    """boolean env var parsing."""

    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "True", "yes", "YES", "on", "ON"):
            monkeypatch.setenv("TEST_BOOL", val)
            assert _bool_env("TEST_BOOL") is True

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("TEST_BOOL", val)
            assert _bool_env("TEST_BOOL") is False

    def test_missing_uses_default(self, monkeypatch):
        monkeypatch.delenv("TEST_BOOL", raising=False)
        assert _bool_env("TEST_BOOL", default=True) is True
        assert _bool_env("TEST_BOOL", default=False) is False


class TestDurationValidation:
    """duration clamping."""

    def test_loop_duration_clamped(self):
        cfg = OramConfig(max_loop_seconds=30.0)
        assert cfg.validate_duration(60.0, kind="loop") == 30.0

    def test_generated_duration_clamped(self):
        cfg = OramConfig(max_generated_seconds=10.0)
        assert cfg.validate_duration(20.0, kind="generated") == 10.0

    def test_negative_duration_clamped(self):
        cfg = OramConfig()
        assert cfg.validate_duration(-5.0) == 0.0

    def test_valid_duration_passes(self):
        cfg = OramConfig()
        assert cfg.validate_duration(10.0) == 10.0
