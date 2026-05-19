"""oram.command.push_to_talk — push-to-talk command audio capture."""

from __future__ import annotations

import numpy as np

from oram.audio.recorder import Recorder


class PushToTalk:
    """manages push-to-talk command capture.

    space toggle: start/stop command audio capture.
    captured audio goes to STT, then parser, then router.
    """

    def __init__(self, sample_rate: int = 48000, max_duration: float = 10.0):
        self._recorder = Recorder(sample_rate=sample_rate, channels=1)
        self._max_duration = max_duration
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def toggle(self, engine=None) -> np.ndarray | None:
        """toggle push-to-talk state.

        if starting: returns None, begins capture.
        if stopping: returns the captured audio buffer.
        """
        if not self._active:
            # start capturing
            if engine is not None and hasattr(engine, "start_command_capture"):
                engine.start_command_capture(max_duration_seconds=self._max_duration)
            else:
                self._recorder.start(max_duration_seconds=self._max_duration)
            self._active = True
            return None
        else:
            # stop and return captured audio
            self._active = False
            if engine is not None and hasattr(engine, "stop_command_capture"):
                buffer = engine.stop_command_capture()
            else:
                buffer = self._recorder.stop()
            if buffer.shape[0] == 0:
                return None
            return buffer

    def feed(self, block: np.ndarray) -> None:
        """feed an audio block during capture."""
        if self._active:
            self._recorder.feed(block)
