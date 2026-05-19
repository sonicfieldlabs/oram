"""oram.audio.playback — immutable playback state for safe callback access.

the audio callback should only read from a PlaybackSnapshot, never mutate
layer state directly. workers publish BufferSwap messages which the control
thread applies between callback blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class LayerSnapshot:
    """immutable view of a single layer for the audio callback."""

    slot: int
    buffer: np.ndarray  # read-only reference
    playhead: int
    volume: float
    pan: float
    muted: bool
    solo: bool
    is_empty: bool
    length_samples: int


@dataclass(frozen=True)
class PlaybackSnapshot:
    """immutable snapshot of all layers for one callback block.

    the control thread builds this and atomically swaps it so the
    callback always reads a consistent state.
    """

    layers: tuple[LayerSnapshot, ...]
    any_solo: bool
    revision: int = 0


@dataclass
class BufferSwap:
    """message from a worker thread to the control thread.

    the control thread applies this between callback blocks.
    """

    layer_slot: int
    new_buffer: np.ndarray
    new_playhead: int = 0
    metadata: dict = field(default_factory=dict)


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
