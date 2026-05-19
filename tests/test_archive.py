"""tests for session archive creation."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from oram.archive.log import read_command_log
from oram.archive.safety import safe_segment, validate_export_path
from oram.archive.session import create_session_folder
from oram.archive.waveform_text import buffer_to_text
from oram.audio.layer import LayerManager
from oram.types import CommandLogEntry, OramSession


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    return tmp_path / "oram_sessions"


@pytest.fixture
def populated_session(session_dir: Path):
    """create a session with some layers for testing."""
    mgr = LayerManager()

    # assign some test buffers
    buf1 = np.random.randn(48000 * 8, 2).astype(np.float32) * 0.3
    mgr.assign_buffer(mgr.layers[0], buf1)

    buf2 = np.random.randn(48000 * 4, 2).astype(np.float32) * 0.2
    mgr.assign_buffer(mgr.layers[1], buf2)

    session = OramSession(
        id="test_001",
        scene="test_scene",
        created_at=datetime(2026, 5, 14, 11, 0, 0),
        sample_rate=48000,
        bpm=72.0,
    )
    session.commands = [
        CommandLogEntry(
            timestamp=datetime(2026, 5, 14, 11, 1, 0),
            raw_text="record eight seconds",
            action_json={"action": "record", "duration": 8.0},
            status="ok",
        ),
        CommandLogEntry(
            timestamp=datetime(2026, 5, 14, 11, 2, 0),
            raw_text="reverse layer one",
            action_json={"action": "apply_effect", "target": 1, "effect": "reverse"},
            status="ok",
        ),
    ]

    return session, mgr


class TestArchive:
    def test_creates_session_folder(self, session_dir, populated_session):
        session, mgr = populated_session
        folder = create_session_folder(session, mgr, session_dir, "oram_0001")

        assert folder.exists()
        assert (folder / "mix.wav").exists()
        assert (folder / "session.json").exists()
        assert (folder / "commands.log").exists()
        assert (folder / "waveform.txt").exists()
        assert (folder / "listening_report.md").exists()
        assert (folder / "stems").is_dir()

    def test_session_json_roundtrip(self, session_dir, populated_session):
        session, mgr = populated_session
        folder = create_session_folder(session, mgr, session_dir, "oram_0001")

        with open(folder / "session.json") as f:
            data = json.load(f)

        assert data["session"] == "oram_0001"
        assert data["scene"] == "test_scene"
        assert data["sample_rate"] == 48000
        assert data["bpm"] == 72.0
        assert len(data["layers"]) == 2
        assert len(data["commands"]) == 2
        assert data["outputs"]["waveform"] == "waveform.txt"
        assert data["outputs"]["listening_report"] == "listening_report.md"

    def test_stems_exported(self, session_dir, populated_session):
        session, mgr = populated_session
        folder = create_session_folder(session, mgr, session_dir, "oram_0001")

        stems = list((folder / "stems").glob("*.wav"))
        assert len(stems) == 2

    def test_command_log_roundtrip(self, session_dir, populated_session):
        session, mgr = populated_session
        folder = create_session_folder(session, mgr, session_dir, "oram_0001")

        entries = read_command_log(folder / "commands.log")
        assert len(entries) == 2
        assert entries[0]["raw"] == "record eight seconds"
        assert entries[1]["action"]["effect"] == "reverse"


class TestWaveformText:
    def test_empty_buffer(self):
        buf = np.zeros((0, 2), dtype=np.float32)
        text = buffer_to_text(buf, width=20)
        assert text == "-" * 20

    def test_generates_characters(self):
        buf = np.random.randn(48000, 2).astype(np.float32) * 0.3
        text = buffer_to_text(buf, width=20)
        assert len(text) == 20
        # should contain at least some non-space characters
        assert text.strip() != ""

    def test_silent_buffer(self):
        buf = np.zeros((48000, 2), dtype=np.float32)
        text = buffer_to_text(buf, width=20)
        assert len(text) == 20


class TestPathSafety:
    """path traversal prevention (§1.5)."""

    def test_session_id_traversal(self, session_dir, populated_session):
        """session IDs containing '../' must be sanitized."""
        session, mgr = populated_session
        # attempt traversal via session_id
        folder = create_session_folder(session, mgr, session_dir, "../../../etc/evil")
        # should be sanitized to the fallback
        assert "etc" not in str(folder)
        assert folder.exists()
        assert folder.parent == session_dir

    def test_layer_name_slash(self, session_dir, populated_session):
        """layer names containing '/' must not create subdirectories in stems."""
        session, mgr = populated_session
        # poison a layer name
        mgr.layers[0].name = "../../etc/passwd"
        folder = create_session_folder(session, mgr, session_dir, "oram_safe")
        # stems should be flat, no subdirectories
        stems = list((folder / "stems").glob("*.wav"))
        for stem in stems:
            assert stem.parent == folder / "stems", (
                f"stem escaped stems dir: {stem}"
            )

    def test_safe_segment_basic(self):
        assert safe_segment("oram_0001") == "oram_0001"
        assert safe_segment("my-session.v2") == "my-session.v2"
        assert safe_segment("../traversal") == "untitled"
        assert safe_segment("/absolute") == "untitled"
        assert safe_segment("") == "untitled"
        assert safe_segment("a" * 100) == "untitled"  # too long

    def test_validate_export_path_within_cwd(self, tmp_path):
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            sub = tmp_path / "sub"
            sub.mkdir()
            result = validate_export_path(sub)
            assert result == sub.resolve()
        finally:
            os.chdir(old_cwd)

    def test_validate_export_path_rejects_outside(self, tmp_path):
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            with pytest.raises(ValueError, match="outside allowed"):
                validate_export_path(Path("/etc/passwd"))
        finally:
            os.chdir(old_cwd)
