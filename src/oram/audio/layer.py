"""oram.audio.layer — layer state machine and operations.

manages up to 4 layers with dynamic creation, lineage tracking,
and mode-specific behavior (recorder, looper, sampler).
"""

from __future__ import annotations

import numpy as np

from oram.constants import MAX_LAYERS
from oram.types import GenerationEngine, Layer, LayerMode, LayerState, ListeningRoute, SourceType


class LayerManager:
    """manages layers with mode behaviors and lineage."""

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.layers: list[Layer] = [
            Layer(
                id=f"layer-{i + 1:03d}",
                name=f"layer_{i + 1}",
                slot=i,
                sample_rate=sample_rate,
                channels=channels,
            )
            for i in range(MAX_LAYERS)
        ]
        self.selected: int = 0  # index into self.layers
        self._undo_buffers: dict[str, np.ndarray] = {}

    @property
    def selected_layer(self) -> Layer:
        return self.layers[self.selected]

    def select(self, layer_num: int) -> Layer:
        """select a layer by 1-based number."""
        if 1 <= layer_num <= len(self.layers):
            self.selected = layer_num - 1
            return self.selected_layer
        raise ValueError(f"invalid layer target: {layer_num}")

    def get_layer(self, target: int | str) -> Layer:
        """resolve a target to a layer.

        accepts:
        - 1-based int
        - 'selected'
        - layer id string like 'layer-001'
        """
        if isinstance(target, int):
            idx = target - 1
            if 0 <= idx < len(self.layers):
                return self.layers[idx]
            raise ValueError(f"invalid layer target: {target}")

        if isinstance(target, str):
            if target == "selected":
                return self.selected_layer

            # try by ID
            for layer in self.layers:
                if layer.id == target:
                    return layer

            # try numeric string
            if target.isdigit():
                idx = int(target) - 1
                if 0 <= idx < len(self.layers):
                    return self.layers[idx]

        raise ValueError(f"invalid layer target: {target}")

    def assign_buffer(self, layer: Layer, buffer: np.ndarray) -> None:
        """assign a recorded buffer to a layer, making it active."""
        buffer = np.asarray(buffer, dtype=np.float32)

        # ensure stereo
        if buffer.ndim == 1:
            buffer = np.column_stack([buffer, buffer])
        elif buffer.ndim == 2:
            if buffer.shape[1] == 0:
                raise ValueError("audio buffer must have at least one channel")
            if buffer.shape[1] == 1:
                buffer = np.column_stack([buffer[:, 0], buffer[:, 0]])
            elif buffer.shape[1] > 2:
                buffer = buffer[:, :2]
        else:
            raise ValueError("audio buffer must be 1D or 2D")

        layer.buffer = buffer.astype(np.float32, copy=False)
        if layer.sample_rate > 0:
            layer.duration_seconds = len(buffer) / layer.sample_rate
        else:
            layer.duration_seconds = 0.0
        layer.playhead = 0
        layer.state = LayerState.ACTIVE
        layer.muted = False
        self._reset_loop_region(layer)
        layer.waveform_revision += 1
        layer.compute_waveform()

    def swap_buffer(self, layer: Layer, new_buf: np.ndarray) -> None:
        """atomically swap a layer's buffer + metadata under its lock.

        used by DSP workers to safely replace audio while the mixer
        reads the buffer.  the GIL makes the single-pointer read safe;
        this method guarantees coherent metadata between reads.
        """
        new_buf = np.asarray(new_buf, dtype=np.float32)
        if new_buf.ndim == 1:
            new_buf = np.column_stack([new_buf, new_buf])
        elif new_buf.ndim == 2 and new_buf.shape[1] == 1:
            new_buf = np.column_stack([new_buf[:, 0], new_buf[:, 0]])
        elif new_buf.ndim == 2 and new_buf.shape[1] > 2:
            new_buf = new_buf[:, :2]

        with layer._buf_lock:
            layer.buffer = new_buf.astype(np.float32, copy=False)
            if layer.sample_rate > 0:
                layer.duration_seconds = len(new_buf) / layer.sample_rate
            else:
                layer.duration_seconds = 0.0
            layer.playhead = 0
            self._clamp_loop_region(layer)
            layer.waveform_revision += 1
        layer.compute_waveform()

    def mute(self, layer: Layer) -> None:
        """toggle mute on a layer."""
        if layer.state == LayerState.EMPTY:
            return
        layer.muted = not layer.muted
        layer.state = LayerState.MUTED if layer.muted else LayerState.ACTIVE

    def solo(self, layer: Layer) -> None:
        """toggle solo on a layer. unsolos all others."""
        if layer.state == LayerState.EMPTY:
            return
        was_solo = layer.solo
        # unsolo everything first
        for l in self.layers:
            l.solo = False
        if not was_solo:
            layer.solo = True

    def clear(self, layer: Layer) -> None:
        """clear a layer's buffer, keeping undo."""
        if layer.state == LayerState.EMPTY:
            return
        # save for undo
        self._undo_buffers[layer.id] = layer.buffer.copy()
        layer.buffer = np.zeros((0, self.channels), dtype=np.float32)
        layer.duration_seconds = 0.0
        layer.playhead = 0
        layer.state = LayerState.EMPTY
        layer.muted = False
        layer.solo = False
        layer.reverse = False
        layer.speed = 1.0
        layer.pitch_semitones = 0.0
        layer.filter_type = None
        layer.filter_cutoff_hz = None
        layer.reverb_amount = 0.0
        layer.grain_density = 0.0
        layer.effects_applied = []
        layer.waveform_data = []
        layer.generation_prompt = None
        layer.parent_layer_id = None
        layer.generation_depth = 0
        layer.is_generated = False
        layer.source_type = SourceType.RECORDED
        self._reset_loop_region(layer)
        layer.waveform_revision += 1

    def undo_clear(self, layer: Layer) -> bool:
        """restore a cleared layer from undo buffer."""
        if layer.id in self._undo_buffers:
            self.assign_buffer(layer, self._undo_buffers.pop(layer.id))
            return True
        return False

    def overdub(self, layer: Layer, new_audio: np.ndarray, gain: float = 0.7) -> None:
        """mix new audio into an existing layer at the current playhead position."""
        if layer.is_empty:
            self.assign_buffer(layer, new_audio)
            return

        # ensure stereo
        if new_audio.ndim == 1:
            new_audio = np.column_stack([new_audio, new_audio])

        buf = layer.buffer.copy()
        start = layer.playhead % buf.shape[0]
        length = min(new_audio.shape[0], buf.shape[0])

        for i in range(length):
            pos = (start + i) % buf.shape[0]
            buf[pos] += new_audio[i] * gain

        # conservative clipping protection
        peak = np.max(np.abs(buf))
        if peak > 0.95:
            buf *= 0.9 / peak

        layer.buffer = buf
        self._clamp_loop_region(layer)
        layer.waveform_revision += 1
        layer.compute_waveform()

    def get_active_layers(self) -> list[Layer]:
        """return layers that should produce sound (respecting solo)."""
        any_solo = any(l.solo for l in self.layers)
        if any_solo:
            return [l for l in self.layers if l.solo and not l.is_empty]
        return [l for l in self.layers if not l.muted and not l.is_empty]

    # --- v2: derivation ---

    def find_empty_layer(self) -> Layer | None:
        """find the first empty layer slot."""
        for layer in self.layers:
            if layer.is_empty:
                return layer
        return None

    def create_derived_layer(
        self,
        parent: Layer,
        audio: np.ndarray,
        route: str,
        engine: str,
        prompt: str,
    ) -> Layer | None:
        """create a new layer derived from a parent.

        returns None if no empty slot is available.
        """
        target = self.find_empty_layer()
        if target is None:
            return None

        self.assign_buffer(target, audio)
        target.source_type = SourceType.GENERATED
        target.is_generated = True
        target.parent_layer_id = parent.id
        target.generation_depth = parent.generation_depth + 1
        target.generation_prompt = prompt
        try:
            target.listening_route = ListeningRoute(route)
        except ValueError:
            target.listening_route = parent.listening_route
        try:
            target.generation_engine = GenerationEngine(engine)
        except ValueError:
            target.generation_engine = parent.generation_engine
        return target

    def replace_layer_audio(self, layer: Layer, new_audio: np.ndarray) -> None:
        """replace a layer's audio while preserving metadata."""
        self._undo_buffers[layer.id] = layer.buffer.copy()
        self.assign_buffer(layer, new_audio)

    def set_loop_region(
        self,
        layer: Layer,
        start_sample: int,
        end_sample: int,
        enabled: bool = True,
    ) -> None:
        """set the loop region for a layer in sample offsets."""
        length = layer.length_samples
        if length <= 0:
            self._reset_loop_region(layer)
            return

        start = max(0, min(int(start_sample), length - 1))
        end = max(start + 1, min(int(end_sample), length))
        layer.looper.start_offset = start
        layer.looper.end_offset = end
        layer.looper.enabled = enabled
        if enabled:
            layer.layer_mode = LayerMode.LOOPER

    @staticmethod
    def _reset_loop_region(layer: Layer) -> None:
        """clear loop-region state for newly assigned or empty audio."""
        layer.looper.start_offset = 0
        layer.looper.end_offset = 0
        layer.looper.enabled = False

    @staticmethod
    def _clamp_loop_region(layer: Layer) -> None:
        """keep loop offsets valid after audio length changes."""
        length = layer.length_samples
        if length <= 0:
            LayerManager._reset_loop_region(layer)
            return

        if not layer.looper.enabled:
            layer.looper.start_offset = max(0, min(int(layer.looper.start_offset), length - 1))
            if layer.looper.end_offset > 0:
                layer.looper.end_offset = max(layer.looper.start_offset + 1, min(int(layer.looper.end_offset), length))
            return

        raw_start = int(layer.looper.start_offset)
        raw_end = int(layer.looper.end_offset) if layer.looper.end_offset > 0 else length
        if raw_start >= length or raw_end <= raw_start:
            start = 0
            end = length
        else:
            start = max(0, min(raw_start, length - 1))
            end = max(start + 1, min(raw_end, length))
        layer.looper.start_offset = start
        layer.looper.end_offset = end

    def fork_layer(self, source: Layer) -> Layer | None:
        """clone a layer into an empty slot with a new ID."""
        target = self.find_empty_layer()
        if target is None:
            return None

        self.assign_buffer(target, source.buffer.copy())
        target.source_type = source.source_type
        target.is_generated = source.is_generated
        target.parent_layer_id = source.parent_layer_id
        target.generation_depth = source.generation_depth
        target.generation_prompt = source.generation_prompt
        target.layer_mode = source.layer_mode
        target.looper.enabled = source.looper.enabled
        target.looper.sync_to_master = source.looper.sync_to_master
        target.looper.free_loop = source.looper.free_loop
        target.looper.start_offset = source.looper.start_offset
        target.looper.end_offset = source.looper.end_offset
        target.looper.fade_in_samples = source.looper.fade_in_samples
        target.looper.fade_out_samples = source.looper.fade_out_samples
        target.looper.reverse = source.looper.reverse
        target.looper.half_speed = source.looper.half_speed
        target.looper.double_speed = source.looper.double_speed
        self._clamp_loop_region(target)
        target.effects_applied = source.effects_applied.copy()
        return target

    def set_layer_mode(self, layer: Layer, mode: LayerMode) -> None:
        """set the behavior mode for a layer."""
        layer.layer_mode = mode
        if mode == LayerMode.LOOPER:
            layer.looper.enabled = True
        elif mode == LayerMode.SAMPLER:
            layer.looper.enabled = False
        elif mode == LayerMode.RECORDER:
            layer.looper.enabled = False

    def get_lineage_chain(self, layer_id: str) -> list[Layer]:
        """get the derivation chain for a layer (oldest first)."""
        chain = []
        layer_map = {l.id: l for l in self.layers}
        current_id: str | None = layer_id

        while current_id and current_id in layer_map:
            chain.append(layer_map[current_id])
            current_id = layer_map[current_id].parent_layer_id

        chain.reverse()
        return chain
