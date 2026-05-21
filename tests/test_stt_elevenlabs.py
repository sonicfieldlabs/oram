"""tests for ElevenLabs STT adapter."""

from __future__ import annotations

import numpy as np
import pytest

from oram.stt.elevenlabs import ElevenLabsSTTAdapter


def test_elevenlabs_stt_requires_api_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    adapter = ElevenLabsSTTAdapter(api_key="")

    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        adapter.transcribe(np.ones((100, 1), dtype=np.float32), 48000)


def test_elevenlabs_stt_empty_audio_returns_empty():
    adapter = ElevenLabsSTTAdapter(api_key="test-key")

    assert adapter.transcribe(np.zeros((0, 1), dtype=np.float32), 48000) == ""
