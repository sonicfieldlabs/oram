"""tests for archive v2 — atomic writes, session metadata, refresh."""

from __future__ import annotations

import json

import numpy as np
import pytest

from oram.archive.session import create_session_folder, refresh_session_folder
from oram.audio.layer import LayerManager
from oram.types import OramSession, SourceType


@pytest.fixture
def session_with_audio(tmp_path):
    """create a session with one recorded and one generated layer."""
    session = OramSession(
        id="test_session",
        scene="test_scene",
        sample_rate=48000,
    )
    manager = LayerManager(sample_rate=48000, channels=2)

    # layer 1: recorded audio
    audio1 = np.random.randn(48000, 2).astype(np.float32) * 0.3
    manager.assign_buffer(manager.layers[0], audio1)
    manager.layers[0].effects_applied = ["reverb"]

    # layer 2: generated audio
    audio2 = np.random.randn(24000, 2).astype(np.float32) * 0.2
    manager.assign_buffer(manager.layers[1], audio2)
    manager.layers[1].source_type = SourceType.GENERATED
    manager.layers[1].is_generated = True
    manager.layers[1].generation_depth = 1
    manager.layers[1].generation_prompt = "distant rain"
    manager.layers[1].parent_layer_id = manager.layers[0].id

    session.layers = manager.layers
    return session, manager, tmp_path


class TestAtomicArchiveWrite:
    """archive uses temp dir + rename for atomicity."""

    def test_creates_session_folder(self, session_with_audio):
        session, manager, tmp_path = session_with_audio
        folder = create_session_folder(session, manager, tmp_path)

        assert folder.exists()
        assert (folder / "session.json").exists()
        assert (folder / "mix.wav").exists()
        assert (folder / "stems").exists()
        assert (folder / "waveform.txt").exists()
        assert (folder / "listening_report.md").exists()
        assert (folder / "commands.log").exists()

    def test_no_temp_dirs_left(self, session_with_audio):
        session, manager, tmp_path = session_with_audio
        create_session_folder(session, manager, tmp_path)

        # no temp dirs should remain
        temps = list(tmp_path.glob(".oram_*"))
        assert len(temps) == 0

    def test_resave_overwrites_cleanly(self, session_with_audio):
        session, manager, tmp_path = session_with_audio
        folder1 = create_session_folder(session, manager, tmp_path, "test_001")
        folder2 = create_session_folder(session, manager, tmp_path, "test_001")

        assert folder1 == folder2
        assert folder1.exists()
        # should have clean files
        data = json.loads((folder1 / "session.json").read_text())
        assert data["session"] == "test_001"


class TestSessionMetadata:
    """session.json contains v2 metadata."""

    def test_source_type_persisted(self, session_with_audio):
        session, manager, tmp_path = session_with_audio
        folder = create_session_folder(session, manager, tmp_path)

        data = json.loads((folder / "session.json").read_text())
        layers = data["layers"]

        # layer 1 should be recorded
        assert layers[0]["source_type"] == "recorded"
        # layer 2 should be generated
        assert layers[1]["source_type"] == "generated"

    def test_generation_metadata_persisted(self, session_with_audio):
        session, manager, tmp_path = session_with_audio
        folder = create_session_folder(session, manager, tmp_path)

        data = json.loads((folder / "session.json").read_text())
        gen_layer = data["layers"][1]

        assert gen_layer["generation_depth"] == 1
        assert gen_layer["generation_prompt"] == "distant rain"
        assert gen_layer["parent_layer_id"] is not None

    def test_effects_persisted(self, session_with_audio):
        session, manager, tmp_path = session_with_audio
        folder = create_session_folder(session, manager, tmp_path)

        data = json.loads((folder / "session.json").read_text())
        assert "reverb" in data["layers"][0]["effects"]


class TestRefreshSessionFolder:
    """refresh rebuilds derived files from stems."""

    def test_refresh_restores_metadata(self, session_with_audio):
        session, manager, tmp_path = session_with_audio
        folder = create_session_folder(session, manager, tmp_path, "refresh_test")

        # refresh should succeed
        refreshed = refresh_session_folder(folder)
        assert refreshed.exists()

        data = json.loads((refreshed / "session.json").read_text())
        assert len(data["layers"]) > 0

    def test_refresh_missing_session_json(self, tmp_path):
        empty_folder = tmp_path / "empty_session"
        empty_folder.mkdir()

        with pytest.raises(FileNotFoundError):
            refresh_session_folder(empty_folder)
