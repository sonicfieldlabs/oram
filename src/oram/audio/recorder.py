"""oram.audio.recorder — input capture to pending buffer, layer assignment."""

from __future__ import annotations

import numpy as np


class Recorder:
    """captures audio input blocks into a pending buffer.

    when recording stops, the buffer is concatenated, optionally normalized,
    converted to stereo, and ready for layer assignment.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._pending: list[np.ndarray] = []
        self._recording = False
        self._max_samples: int | None = None
        self._total_samples: int = 0

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self, max_duration_seconds: float | None = None) -> None:
        """start recording."""
        self._pending = []
        self._recording = True
        self._total_samples = 0
        if max_duration_seconds is not None:
            self._max_samples = int(max_duration_seconds * self.sample_rate)
        else:
            self._max_samples = int(120.0 * self.sample_rate)

    def feed(self, block: np.ndarray) -> bool:
        """feed a block of audio into the recorder.

        returns False if max duration reached (recording should stop).
        """
        if not self._recording:
            return False

        self._pending.append(block.copy())
        self._total_samples += block.shape[0]

        if self._max_samples and self._total_samples >= self._max_samples:
            return False

        return True

    def stop(self) -> np.ndarray:
        """stop recording and return the concatenated buffer.

        normalizes conservatively and converts to stereo.
        """
        self._recording = False

        if not self._pending:
            return np.zeros((0, self.channels), dtype=np.float32)

        buffer = np.concatenate(self._pending, axis=0)
        self._pending = []

        # trim to max samples
        if self._max_samples and buffer.shape[0] > self._max_samples:
            buffer = buffer[: self._max_samples]

        # convert to stereo
        if buffer.ndim == 1:
            buffer = np.column_stack([buffer, buffer])
        elif buffer.shape[1] == 1:
            buffer = np.column_stack([buffer[:, 0], buffer[:, 0]])

        # conservative normalization: only if peak > 0.95
        peak = np.max(np.abs(buffer))
        if peak > 0.95:
            buffer = buffer * (0.9 / peak)

        return buffer.astype(np.float32)
