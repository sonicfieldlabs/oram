"""master bus recording helpers."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


class MasterBusRecorder:
    """stream copied master-output blocks to a WAV file on a worker thread."""

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        queue_blocks: int = 1024,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self._queue_blocks = queue_blocks
        self._queue: queue.Queue[np.ndarray | None] | None = None
        self._thread: threading.Thread | None = None
        self._path: Path | None = None
        self._active = False
        self._samples_written = 0
        self._dropped_blocks = 0
        self._started_at = 0.0
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._active

    @property
    def elapsed_seconds(self) -> float:
        if not self._active or self._started_at <= 0:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    def start(self, path: Path) -> None:
        with self._lock:
            if self._active:
                raise RuntimeError("master recording is already active")

            self._path = path
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._queue = queue.Queue(maxsize=self._queue_blocks)
            self._samples_written = 0
            self._dropped_blocks = 0
            self._started_at = time.monotonic()
            self._active = True
            self._thread = threading.Thread(target=self._write_loop, daemon=True)
            self._thread.start()

    def write(self, block: np.ndarray) -> None:
        if not self._active or self._queue is None:
            return
        try:
            self._queue.put_nowait(np.asarray(block, dtype=np.float32).copy())
        except queue.Full:
            self._dropped_blocks += 1

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._active:
                raise RuntimeError("master recording is not active")
            self._active = False
            q = self._queue
            thread = self._thread

        if q is not None:
            q.put(None)
        if thread is not None:
            thread.join(timeout=10.0)

        duration = (
            self._samples_written / self.sample_rate
            if self.sample_rate > 0
            else 0.0
        )
        result = {
            "path": str(self._path) if self._path else "",
            "samples": self._samples_written,
            "duration": round(duration, 3),
            "dropped_blocks": self._dropped_blocks,
        }
        with self._lock:
            self._queue = None
            self._thread = None
            self._path = None
            self._started_at = 0.0
        return result

    def abort(self) -> None:
        if not self._active:
            return
        try:
            self.stop()
        except Exception:
            self._active = False

    def _write_loop(self) -> None:
        path = self._path
        q = self._queue
        if path is None or q is None:
            return

        with sf.SoundFile(
            str(path),
            mode="w",
            samplerate=self.sample_rate,
            channels=self.channels,
            subtype="FLOAT",
        ) as wav:
            while True:
                block = q.get()
                if block is None:
                    break
                if block.ndim == 1:
                    block = np.column_stack([block, block])
                if block.shape[1] < self.channels:
                    block = np.repeat(block[:, :1], self.channels, axis=1)
                elif block.shape[1] > self.channels:
                    block = block[:, :self.channels]
                wav.write(block)
                self._samples_written += int(block.shape[0])
