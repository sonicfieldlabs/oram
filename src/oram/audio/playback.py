"""oram.audio.playback — pre-allocated capture buffers for the audio callback.

the realtime engine records into RingBuffers so the callback never grows a
python list.  (the snapshot/message-queue playback design that used to live
here was never wired in — the mixer's per-layer locks are the real mechanism.)
"""

from __future__ import annotations

import numpy as np


class RingBuffer:
    """pre-allocated ring buffer for recording/command capture.

    avoids unbounded list appends in the audio callback.
    """

    def __init__(self, max_samples: int, channels: int = 2):
        self._buffer = np.zeros((max_samples, channels), dtype=np.float32)
        self._write_pos = 0
        self._max_samples = max_samples
        self._channels = channels

    @property
    def samples_written(self) -> int:
        return self._write_pos

    @property
    def is_full(self) -> bool:
        return self._write_pos >= self._max_samples

    def write(self, data: np.ndarray) -> int:
        """write data to the ring buffer. returns samples actually written."""
        available = self._max_samples - self._write_pos
        if available <= 0:
            return 0
        n = min(data.shape[0], available)
        self._buffer[self._write_pos:self._write_pos + n] = data[:n]
        self._write_pos += n
        return n

    def read(self) -> np.ndarray:
        """read all written data and reset."""
        if self._write_pos == 0:
            return np.zeros((0, self._channels), dtype=np.float32)
        data = self._buffer[:self._write_pos].copy()
        self._write_pos = 0
        return data

    def reset(self) -> None:
        self._write_pos = 0
