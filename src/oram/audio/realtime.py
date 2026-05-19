"""oram.audio.realtime — real audio engine using sounddevice.

captures input from the system microphone and plays back
mixed layers through the system output. uses a callback-based
approach for low-latency I/O.

SAFETY RULES:
- audio callback must not allocate, spawn threads, or do I/O.
- recording uses pre-allocated RingBuffer instead of list.append.
- buffer swaps are atomic reference swaps between callback blocks.
"""

from __future__ import annotations

import queue
import threading

import numpy as np
import sounddevice as sd

from oram.audio.layer import LayerManager
from oram.audio.mixer import Mixer, MixerWorkspace
from oram.audio.playback import RingBuffer
from oram.types import LayerState, OramSession

# default max recording duration (seconds) for pre-allocated ring buffers
_MAX_RECORD_SECONDS = 120.0
_MAX_COMMAND_SECONDS = 10.0


class RealAudioEngine:
    """real audio engine using sounddevice for hardware I/O."""

    def __init__(
        self,
        session: OramSession,
        layer_manager: LayerManager,
        sample_rate: int = 48000,
        block_size: int = 512,
        input_device: int | None = None,
        output_device: int | None = None,
        on_record_complete=None,
    ):
        self.session = session
        self.layers = layer_manager
        self.mixer = Mixer(sample_rate=sample_rate, channels=2)
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.input_device = input_device
        self.output_device = output_device
        self.command_queue: queue.Queue = queue.Queue()
        self._on_record_complete = on_record_complete

        self._running = False
        self._stream: sd.Stream | None = None
        self._input_level: float = 0.0
        self._output_level: float = 0.0

        # --- pre-allocated mixer workspace (§1.2) ---
        self._workspace = MixerWorkspace.create(block_size, channels=2)

        # --- recording state (§1.3) ---
        # pre-allocate ring buffers once; start_recording/start_command_capture
        # just reset them and flip a boolean flag. the callback only reads the
        # flag + the (always-valid) buffer reference — no lock needed.
        self._recording = False
        self._record_target: int | None = None
        self._record_ring = RingBuffer(
            int(_MAX_RECORD_SECONDS * sample_rate), channels=2,
        )
        self._overdub_mode = False

        # command capture — pre-allocated mono ring
        self._command_capture = False
        self._command_ring = RingBuffer(
            int(_MAX_COMMAND_SECONDS * sample_rate), channels=1,
        )
        self._command_mono_scratch = np.zeros((block_size, 1), dtype=np.float32)

        # auto-stop flag set by callback, processed by control thread
        self._auto_stop_pending = False

        # lock protects control-plane state transitions (not callback reads)
        self._control_lock = threading.Lock()

    def start(self) -> None:
        """start the audio stream."""
        self._running = True
        self._has_input = False

        # detect if we have an input device
        has_input_device = False
        if self.input_device is not None:
            has_input_device = True
        else:
            try:
                default_in = sd.default.device[0]
                if default_in >= 0:
                    info = sd.query_devices(default_in)
                    if info["max_input_channels"] > 0:
                        has_input_device = True
            except Exception:
                pass

        if has_input_device:
            try:
                self._stream = sd.Stream(
                    samplerate=self.sample_rate,
                    blocksize=self.block_size,
                    device=(self.input_device, self.output_device),
                    channels=2,
                    dtype="float32",
                    callback=self._audio_callback,
                    finished_callback=self._stream_finished,
                )
                self._stream.start()
                self._has_input = True
                print("audio: duplex (input + output)")

                # start auto-stop polling thread
                self._start_auto_stop_poller()
                return
            except Exception as e:
                print(f"duplex failed: {e}")

        # output only
        try:
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                device=self.output_device,
                channels=2,
                dtype="float32",
                callback=self._output_only_callback,
            )
            self._stream.start()
            print("audio: output only (no input device)")
        except Exception as e2:
            print(f"output error: {e2}")
            self._running = False

    def _start_auto_stop_poller(self) -> None:
        """poll for auto-stop flag set by the callback."""
        def _poll():
            while self._running:
                if self._auto_stop_pending:
                    self._auto_stop_pending = False
                    self.stop_recording()
                threading.Event().wait(0.05)  # 50ms poll
        t = threading.Thread(target=_poll, daemon=True)
        t.start()

    def stop(self) -> None:
        """stop the audio stream."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

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
        with self._control_lock:
            # reset the pre-allocated ring buffer (no allocation)
            self._record_ring.reset()
            self._record_target = target
            self._overdub_mode = overdub

            # mark layer as recording
            layer = self.layers.get_layer(
                target if target is not None else "selected"
            )
            layer.state = LayerState.RECORDING

            # flip the flag last — this is the atomic transition
            # the callback observes
            self._recording = True

    def start_command_capture(self, max_duration_seconds: float = 10.0) -> None:
        """capture microphone audio for push-to-talk command transcription."""
        with self._control_lock:
            self._command_ring.reset()
            # flip flag last
            self._command_capture = True

    def stop_command_capture(self) -> np.ndarray:
        """stop command capture and return mono command audio."""
        with self._control_lock:
            self._command_capture = False
            buffer = self._command_ring.read()
        return buffer.astype(np.float32)

    def stop_recording(self) -> np.ndarray | None:
        with self._control_lock:
            if not self._recording:
                return None
            self._recording = False
            target = self._record_target if self._record_target is not None else "selected"
            layer = self.layers.get_layer(target)
            if self._record_ring.samples_written == 0:
                layer.state = LayerState.EMPTY if layer.is_empty else (
                    LayerState.MUTED if layer.muted else LayerState.ACTIVE
                )
                return None

            buffer = self._record_ring.read()

        if self._overdub_mode:
            self.layers.overdub(layer, buffer)
        else:
            self.layers.assign_buffer(layer, buffer)

        # restore layer state
        layer.state = LayerState.ACTIVE

        # trigger callback for auto-listen/generate — in a separate thread,
        # but NOT from the audio callback
        if self._on_record_complete and not self._overdub_mode:
            threading.Thread(
                target=self._on_record_complete,
                args=(layer,),
                daemon=True,
            ).start()

        return buffer

    def _audio_callback(self, indata, outdata, frames, time_info, status):
        """duplex audio callback — captures input and plays output.

        SAFETY: no allocations, no thread spawns, no I/O.
        Uses pre-allocated ring buffers and mixer workspace.
        """
        # reject oversized frames (don't raise — would crash sounddevice)
        if frames > self.block_size:
            outdata[:] = 0.0
            print(f"audio: frames ({frames}) > block_size ({self.block_size}), dropped")
            return

        # input: capture for recording and level metering
        if indata is not None:
            self._input_level = float(max(np.max(indata), -np.min(indata)))

            if self._recording:
                self._record_ring.write(indata)
                # signal auto-stop via flag (no thread spawn!)
                if self._record_ring.is_full:
                    self._auto_stop_pending = True

            if self._command_capture:
                if frames <= self._command_mono_scratch.shape[0] and indata.shape[1] >= 2:
                    mono = self._command_mono_scratch[:frames]
                    np.add(indata[:, 0:1], indata[:, 1:2], out=mono)
                    mono *= 0.5
                else:
                    mono = self._command_mono_scratch[:frames]
                    mono[:] = indata[:, 0:1]
                self._command_ring.write(mono)
                if self._command_ring.is_full:
                    self._command_capture = False
        else:
            self._input_level *= 0.95

        # output: mix active layers using pre-allocated workspace
        active = self.layers.get_active_layers()
        if active:
            mixed = self.mixer.mix_block(active, frames, out=self._workspace.master)
            outdata[:frames] = mixed[:frames]
            self._output_level = float(max(np.max(outdata), -np.min(outdata)))
        else:
            outdata[:] = 0.0
            self._output_level *= 0.95

        # advance playheads
        self.mixer.advance_playheads(self.layers.layers, frames)

    def _output_only_callback(self, outdata, frames, time_info, status):
        """output-only callback when input is unavailable."""
        if frames > self.block_size:
            outdata[:] = 0.0
            return

        active = self.layers.get_active_layers()
        if active:
            mixed = self.mixer.mix_block(active, frames, out=self._workspace.master)
            outdata[:frames] = mixed[:frames]
            self._output_level = float(max(np.max(outdata), -np.min(outdata)))
        else:
            outdata[:] = 0.0
            self._output_level *= 0.95

        self.mixer.advance_playheads(self.layers.layers, frames)

    def _stream_finished(self):
        self._running = False
