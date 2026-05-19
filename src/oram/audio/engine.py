"""oram.audio.engine — audio engine protocol and mock implementation."""

from __future__ import annotations

import queue
import threading
import time
from typing import Protocol

import numpy as np

from oram.audio.layer import LayerManager
from oram.audio.mixer import Mixer
from oram.types import LayerState, OramSession


class AudioEngine(Protocol):
    """protocol for audio engines."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def get_input_level(self) -> float: ...
    def get_output_level(self) -> float: ...
    def start_command_capture(self, max_duration_seconds: float = 10.0) -> None: ...
    def stop_command_capture(self) -> np.ndarray: ...


class MockAudioEngine:
    """mock audio engine for development without audio hardware.

    simulates callback timing with synthetic buffers. processes the command
    queue on a control thread.
    """

    def __init__(
        self,
        session: OramSession,
        layer_manager: LayerManager,
        sample_rate: int = 48000,
        block_size: int = 512,
        on_record_complete=None,
    ):
        self.session = session
        self.layers = layer_manager
        self.mixer = Mixer(sample_rate=sample_rate, channels=2)
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.command_queue: queue.Queue = queue.Queue()
        self._on_record_complete = on_record_complete

        self._running = False
        self._thread: threading.Thread | None = None
        self._input_level: float = 0.0
        self._output_level: float = 0.0

        # recording state
        self._recording = False
        self._record_target: int | None = None
        self._record_buffer: list[np.ndarray] = []
        self._record_max_samples: int | None = None
        self._record_samples: int = 0
        self._overdub_mode = False
        self._command_capture = False
        self._command_buffer: list[np.ndarray] = []
        self._command_max_samples: int | None = None
        self._command_samples: int = 0

    def start(self) -> None:
        """start the mock audio engine."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """stop the mock audio engine."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def is_running(self) -> bool:
        return self._running

    def get_input_level(self) -> float:
        return self._input_level

    def get_output_level(self) -> float:
        return self._output_level

    def start_recording(
        self,
        target: int | None = None,
        duration_seconds: float | None = None,
        overdub: bool = False,
    ) -> None:
        """begin recording into a layer."""
        self._recording = True
        self._record_target = target
        self._record_buffer = []
        self._record_samples = 0
        self._overdub_mode = overdub
        if duration_seconds is not None:
            self._record_max_samples = int(duration_seconds * self.sample_rate)
        else:
            self._record_max_samples = int(120.0 * self.sample_rate)  # max

        # mark layer as recording
        layer = self.layers.get_layer(
            target if target is not None else "selected"
        )
        layer.state = LayerState.RECORDING

    def start_command_capture(self, max_duration_seconds: float = 10.0) -> None:
        """capture microphone audio for push-to-talk command transcription."""
        self._command_capture = True
        self._command_buffer = []
        self._command_samples = 0
        self._command_max_samples = int(max_duration_seconds * self.sample_rate)

    def stop_command_capture(self) -> np.ndarray:
        """stop command capture and return mono command audio."""
        self._command_capture = False
        if not self._command_buffer:
            return np.zeros((0, 1), dtype=np.float32)
        buffer = np.concatenate(self._command_buffer, axis=0)
        self._command_buffer = []
        return buffer.astype(np.float32)

    def stop_recording(self) -> np.ndarray | None:
        """stop recording and return the captured buffer."""
        if not self._recording:
            return None
        self._recording = False
        layer = self.layers.get_layer(
            self._record_target if self._record_target is not None else "selected"
        )
        if not self._record_buffer:
            layer.state = LayerState.EMPTY if layer.is_empty else (
                LayerState.MUTED if layer.muted else LayerState.ACTIVE
            )
            return None
        buffer = np.concatenate(self._record_buffer, axis=0)
        self._record_buffer = []

        if self._overdub_mode:
            self.layers.overdub(layer, buffer)
        else:
            self.layers.assign_buffer(layer, buffer)

        # restore layer state
        layer.state = LayerState.ACTIVE

        # trigger callback for auto-listen/generate
        if self._on_record_complete and not self._overdub_mode:
            threading.Thread(
                target=self._on_record_complete,
                args=(layer,),
                daemon=True,
            ).start()

        return buffer

    def _run_loop(self) -> None:
        """main mock engine loop — simulates audio callback timing."""
        block_duration = self.block_size / self.sample_rate

        while self._running:
            start = time.monotonic()

            # process pending commands
            while not self.command_queue.empty():
                try:
                    cmd = self.command_queue.get_nowait()
                    self._process_command(cmd)
                except queue.Empty:
                    break

            # simulate input (quiet noise for level meter and mock capture activity)
            if self._recording or self._command_capture:
                block = np.random.randn(self.block_size, 2).astype(np.float32) * 0.01
                self._input_level = float(np.max(np.abs(block)))

            if self._recording:
                self._record_buffer.append(block)
                self._record_samples += self.block_size

                # check fixed-duration recording
                if (
                    self._record_max_samples is not None
                    and self._record_samples >= self._record_max_samples
                ):
                    self.stop_recording()

            if self._command_capture:
                mono = np.mean(block, axis=1, keepdims=True)
                self._command_buffer.append(mono.copy())
                self._command_samples += self.block_size
                if (
                    self._command_max_samples is not None
                    and self._command_samples >= self._command_max_samples
                ):
                    self._command_capture = False
            else:
                self._input_level = max(0.0, self._input_level * 0.95)

            # simulate output mixing
            active = self.layers.get_active_layers()
            if active:
                mixed = self.mixer.mix_block(active, self.block_size)
                self._output_level = float(np.max(np.abs(mixed)))
            else:
                self._output_level = max(0.0, self._output_level * 0.95)

            # advance playheads
            self.mixer.advance_playheads(self.layers.layers, self.block_size)

            # sleep to simulate realtime
            elapsed = time.monotonic() - start
            sleep_time = block_duration - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _process_command(self, cmd: dict) -> None:
        """process a command from the queue."""
        # commands are dicts with at minimum an 'action' key
        pass  # routing handled by the command router
