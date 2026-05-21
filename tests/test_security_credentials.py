"""credential store and redaction tests."""

from __future__ import annotations

import json

import numpy as np

from oram.archive.session import create_session_folder
from oram.audio.layer import LayerManager
from oram.types import CommandLogEntry, OramSession
from oram_security.credentials import ChainedCredentialStore, EnvCredentialStore, MemoryCredentialStore
from oram_security.redaction import redact_mapping, redact_text


def test_memory_credential_status_never_contains_secret():
    store = MemoryCredentialStore({"elevenlabs": "unit-test-provider-key"})
    status = store.status("elevenlabs").as_dict()
    assert status["configured"] is True
    assert status["source"] == "memory"
    assert "unit-test-provider-key" not in str(status)


def test_chained_store_prefers_primary_then_env(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "unit-test-env-key")
    store = ChainedCredentialStore(MemoryCredentialStore(), [EnvCredentialStore()])
    assert store.get_secret("elevenlabs") == "unit-test-env-key"
    store.set_secret("elevenlabs", "unit-test-memory-key")
    assert store.get_secret("elevenlabs") == "unit-test-memory-key"


def test_redacts_headers_and_bearer_tokens():
    text = "xi-api-key: unit-test-provider-key Authorization: Bearer abcdefghijklmnop"
    redacted = redact_text(text)
    assert "unit-test-provider-key" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "[redacted]" in redacted


def test_redacts_nested_mapping():
    payload = {
        "provider": "elevenlabs",
        "api_key": "unit-test-provider-key",
        "headers": {"Authorization": "Bearer abcdefghijklmnop"},
    }
    redacted = redact_mapping(payload)
    assert "unit-test-provider-key" not in str(redacted)
    assert "abcdefghijklmnop" not in str(redacted)


def test_session_archive_redacts_command_secrets(tmp_path):
    secret = "unit-test-provider-key"
    session = OramSession(id="test", scene="test")
    session.commands.append(
        CommandLogEntry(
            timestamp=session.created_at,
            raw_text=f"set api_key={secret}",
            action_json={"action": "unknown", "api_key": secret},
            status="ok",
            message=f"Authorization: Bearer {secret}",
        )
    )
    manager = LayerManager()
    manager.assign_buffer(manager.layers[0], np.zeros((256, 2), dtype=np.float32))
    manager.layers[0].generation_prompt = f"prompt token={secret}"
    folder = create_session_folder(session, manager, tmp_path, "oram_test")

    for path in ("session.json", "commands.log", "listening_report.md"):
        content = (folder / path).read_text(encoding="utf-8")
        assert secret not in content

    session_data = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert "api_key" not in json.dumps(session_data).lower()
