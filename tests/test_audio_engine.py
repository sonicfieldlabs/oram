"""tests for audio engine fallback behavior."""

from __future__ import annotations

import pytest

from oram.audio.engine import UnavailableAudioEngine


def test_unavailable_audio_engine_is_silent_and_not_running():
    engine = UnavailableAudioEngine("permission denied")

    engine.start()

    assert engine.is_running() is False
    assert engine.has_input() is False
    assert engine.get_input_level() == 0.0
    assert engine.get_output_level() == 0.0

    with pytest.raises(RuntimeError, match="real audio unavailable"):
        engine.start_recording()

    with pytest.raises(RuntimeError, match="real audio unavailable"):
        engine.start_command_capture()

    assert engine.stop_recording() is None
    assert engine.stop_command_capture().shape == (0, 1)
