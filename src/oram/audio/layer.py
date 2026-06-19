"""oram.audio.layer — layer state machine and operations.

manages up to 4 layers with dynamic creation, lineage tracking,
and mode-specific behavior (recorder, looper, sampler).
"""

from __future__ import annotations

import numpy as np

from oram.constants import MAX_LAYERS
from oram.dsp.safety import coerce_audio_buffer
from oram.types import GenerationEngine, Layer, LayerMode, LayerState, ListeningRoute, SourceType


class LayerManager:
    """manages layers with mode behaviors and lineage."""

    def __init__(self, sample_rate: int = 44100, channels: int = 2):
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
        buffer = coerce_audio_buffer(buffer)
        if buffer.shape[0] == 0:
            raise ValueError("audio buffer must contain at least one sample")

        with layer._buf_lock:
            layer.buffer = buffer
            if layer.sample_rate > 0:
                layer.duration_seconds = len(buffer) / layer.sample_rate
            else:
                layer.duration_seconds = 0.0
            layer.playhead = 0
            layer.state = LayerState.ACTIVE
            layer.muted = False
            self._reset_playback_modifiers(layer)
            self._reset_loop_region(layer)
            layer.waveform_revision += 1
        layer.compute_waveform()

    def swap_buffer(
        self,
        layer: Layer,
        new_buf: np.ndarray,
        *,
        preserve_playhead: bool = True,
        source_playhead: int | None = None,
        source_length: int | None = None,
        metadata_update=None,
    ) -> None:
        """atomically swap a layer's buffer + metadata under its lock.

        used by DSP workers to safely replace audio while the mixer
        reads the buffer.  the GIL makes the single-pointer read safe;
        this method guarantees coherent metadata between reads.
        """
        new_buf = coerce_audio_buffer(new_buf)
        if new_buf.shape[0] == 0:
            raise ValueError("audio buffer must contain at least one sample")

        with layer._buf_lock:
            old_length = source_length if source_length is not None else layer.length_samples
            old_playhead = source_playhead if source_playhead is not None else layer.playhead
            layer.buffer = new_buf.astype(np.float32, copy=False)
            if layer.sample_rate > 0:
                layer.duration_seconds = len(new_buf) / layer.sample_rate
            else:
                layer.duration_seconds = 0.0
            if preserve_playhead:
                layer.playhead = self._project_playhead(old_playhead, old_length, len(new_buf))
            else:
                layer.playhead = 0
            if metadata_update is not None:
                metadata_update(layer)
            self._clamp_loop_region(layer)
            layer.waveform_revision += 1
        layer.compute_waveform()

    @staticmethod
    def _project_playhead(old_playhead: int, old_length: int | None, new_length: int) -> int:
        """Map a playhead into a resized buffer without jumping to the start."""
        if new_length <= 0 or not old_length or old_length <= 0:
            return 0
        phase = (int(old_playhead) % int(old_length)) / float(old_length)
        return min(new_length - 1, max(0, int(phase * new_length)))

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
        with layer._buf_lock:
            self._undo_buffers[layer.id] = layer.buffer.copy()
            layer.buffer = np.zeros((0, self.channels), dtype=np.float32)
            layer.duration_seconds = 0.0
            layer.playhead = 0
            layer.state = LayerState.EMPTY
            layer.muted = False
            layer.solo = False
            self._reset_playback_modifiers(layer)
            layer.waveform_data = []
            layer.generation_prompt = None
            layer.parent_layer_id = None
            layer.generation_depth = 0
            layer.is_generated = False
            layer.source_type = SourceType.RECORDED
            layer.inpaint_regions = []
            self._reset_loop_region(layer)
            layer.waveform_revision += 1

    def silence_all(self) -> list[str]:
        """force every non-empty layer into a silent playback state."""
        results: list[str] = []
        for layer in self.layers:
            with layer._buf_lock:
                layer.solo = False
                layer.playhead = 0
                if layer.is_empty:
                    layer.muted = False
                    continue
                if not layer.muted or layer.state != LayerState.MUTED:
                    results.append(f"silenced layer {layer.slot + 1}")
                layer.muted = True
                layer.state = LayerState.MUTED
        return results

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

        with layer._buf_lock:
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

        with layer._buf_lock:
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
        target: Layer | None = None,
    ) -> Layer | None:
        """create a new layer derived from a parent.

        returns None if no empty slot is available.
        """
        target = target or self.find_empty_layer()
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

    def assign_generated_buffer(
        self,
        layer: Layer,
        audio: np.ndarray,
        *,
        prompt: str,
        provider: str = "",
        parent: Layer | None = None,
        route: str | None = None,
        engine: str | None = None,
        volume: float | None = None,
    ) -> Layer:
        """Assign generated audio and metadata in one layer lock."""
        audio = coerce_audio_buffer(audio)
        if audio.shape[0] == 0:
            raise ValueError("audio buffer must contain at least one sample")

        with layer._buf_lock:
            layer.buffer = audio
            layer.duration_seconds = len(audio) / layer.sample_rate if layer.sample_rate > 0 else 0.0
            layer.playhead = 0
            layer.state = LayerState.ACTIVE
            layer.muted = False
            self._reset_playback_modifiers(layer)
            if volume is not None:
                layer.volume = float(volume)
            self._reset_loop_region(layer)
            layer.source_type = SourceType.GENERATED
            layer.is_generated = True
            layer.generation_prompt = prompt
            layer.engine_provider = provider
            if parent is not None:
                layer.parent_layer_id = parent.id
                layer.generation_depth = parent.generation_depth + 1
                layer.listening_route = parent.listening_route
                layer.generation_engine = parent.generation_engine
            else:
                layer.parent_layer_id = None
                layer.generation_depth = 0
            if route is not None:
                try:
                    layer.listening_route = ListeningRoute(route)
                except ValueError:
                    pass
            if engine is not None:
                try:
                    layer.generation_engine = GenerationEngine(engine)
                except ValueError:
                    pass
            layer.waveform_revision += 1
        layer.compute_waveform()
        return layer

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
        with layer._buf_lock:
            length = layer.length_samples
            if length <= 0:
                self._reset_loop_region(layer)
                return

            start = max(0, min(int(start_sample), length - 1))
            end = max(start + 1, min(int(end_sample), length))
            layer.looper.start_offset = start
            layer.looper.end_offset = end
            layer.looper.enabled = enabled
            if not enabled:
                layer.looper.fade_in_samples = 0
                layer.looper.fade_out_samples = 0
            self._clamp_loop_fades(layer)
            if enabled:
                layer.layer_mode = LayerMode.LOOPER

    def set_loop_fades(
        self,
        layer: Layer,
        fade_in_samples: int | None = None,
        fade_out_samples: int | None = None,
    ) -> None:
        """set loop fade lengths in samples."""
        with layer._buf_lock:
            length = layer.length_samples
            if length <= 0:
                layer.looper.fade_in_samples = 0
                layer.looper.fade_out_samples = 0
                return

            if fade_in_samples is not None:
                layer.looper.fade_in_samples = max(0, int(fade_in_samples))
            if fade_out_samples is not None:
                layer.looper.fade_out_samples = max(0, int(fade_out_samples))
            self._clamp_loop_fades(layer)
            if layer.looper.enabled:
                layer.layer_mode = LayerMode.LOOPER

    def set_playback_reverse(self, layer: Layer, enabled: bool) -> None:
        """toggle non-destructive reverse playback for all layer playback modes."""
        reverse = bool(enabled)
        with layer._buf_lock:
            layer.reverse = reverse
            layer.looper.reverse = reverse
            layer.sampler.reverse = reverse

    def set_inpaint_regions(
        self,
        layer: Layer,
        regions: list[tuple[int, int]],
    ) -> None:
        """store non-destructive inpaint regions as sample ranges."""
        with layer._buf_lock:
            length = layer.length_samples
            if length <= 0:
                layer.inpaint_regions = []
                return

            sanitized: list[tuple[int, int]] = []
            for raw_start, raw_end in regions:
                start = max(0, min(int(raw_start), length - 1))
                end = max(start + 1, min(int(raw_end), length))
                if end > start:
                    sanitized.append((start, end))
            layer.inpaint_regions = sanitized

    @staticmethod
    def _reset_playback_modifiers(layer: Layer) -> None:
        """clear per-buffer playback/DSP state when fresh audio is assigned."""
        layer.reverse = False
        layer.looper.reverse = False
        layer.sampler.reverse = False
        layer.speed = 1.0
        layer.pitch_semitones = 0.0
        layer.filter_type = None
        layer.filter_cutoff_hz = None
        layer.reverb_amount = 0.0
        layer.grain_density = 0.0
        layer.grain_size_ms = 120.0
        layer.grain_jitter = 0.0
        layer.effects_applied = []

    @staticmethod
    def _reset_loop_region(layer: Layer) -> None:
        """clear loop-region state for newly assigned or empty audio."""
        layer.looper.start_offset = 0
        layer.looper.end_offset = 0
        layer.looper.enabled = False
        layer.looper.fade_in_samples = 0
        layer.looper.fade_out_samples = 0
        layer.inpaint_regions = []

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
            LayerManager._clamp_loop_fades(layer)
            LayerManager._clamp_inpaint_regions(layer)
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
        LayerManager._clamp_loop_fades(layer)
        LayerManager._clamp_inpaint_regions(layer)

    @staticmethod
    def _clamp_loop_fades(layer: Layer) -> None:
        """keep loop fades inside the current loop length."""
        length = layer.length_samples
        if length <= 0:
            layer.looper.fade_in_samples = 0
            layer.looper.fade_out_samples = 0
            return

        start = max(0, min(int(layer.looper.start_offset), length - 1))
        end = int(layer.looper.end_offset) if layer.looper.end_offset > 0 else length
        end = min(max(start + 1, end), length)
        loop_len = max(1, end - start)
        max_fade = max(0, loop_len - 1)
        layer.looper.fade_in_samples = max(0, min(int(layer.looper.fade_in_samples), max_fade))
        layer.looper.fade_out_samples = max(0, min(int(layer.looper.fade_out_samples), max_fade))

    @staticmethod
    def _clamp_inpaint_regions(layer: Layer) -> None:
        """keep inpaint ranges valid after audio length changes."""
        length = layer.length_samples
        if length <= 0:
            layer.inpaint_regions = []
            return
        sanitized: list[tuple[int, int]] = []
        for raw_start, raw_end in layer.inpaint_regions:
            start = max(0, min(int(raw_start), length - 1))
            end = max(start + 1, min(int(raw_end), length))
            if end > start:
                sanitized.append((start, end))
        layer.inpaint_regions = sanitized

    def fork_layer(self, source: Layer) -> Layer | None:
        """clone a layer into an empty slot with a new ID."""
        target = self.find_empty_layer()
        if target is None:
            return None

        with source._buf_lock:
            source_audio = source.buffer.copy()
            source_metadata = {
                "source_type": source.source_type,
                "is_generated": source.is_generated,
                "parent_layer_id": source.parent_layer_id,
                "generation_depth": source.generation_depth,
                "generation_prompt": source.generation_prompt,
                "layer_mode": source.layer_mode,
                "looper_enabled": source.looper.enabled,
                "looper_sync_to_master": source.looper.sync_to_master,
                "looper_free_loop": source.looper.free_loop,
                "looper_start_offset": source.looper.start_offset,
                "looper_end_offset": source.looper.end_offset,
                "looper_fade_in_samples": source.looper.fade_in_samples,
                "looper_fade_out_samples": source.looper.fade_out_samples,
                "looper_reverse": source.looper.reverse,
                "looper_half_speed": source.looper.half_speed,
                "looper_double_speed": source.looper.double_speed,
                "reverse": source.reverse,
                "sampler_reverse": source.sampler.reverse,
                "speed": source.speed,
                "pitch_semitones": source.pitch_semitones,
                "filter_type": source.filter_type,
                "filter_cutoff_hz": source.filter_cutoff_hz,
                "reverb_amount": source.reverb_amount,
                "grain_density": source.grain_density,
                "grain_size_ms": source.grain_size_ms,
                "grain_jitter": source.grain_jitter,
                "inpaint_regions": source.inpaint_regions.copy(),
                "effects_applied": source.effects_applied.copy(),
            }

        self.assign_buffer(target, source_audio)
        with target._buf_lock:
            target.source_type = source_metadata["source_type"]
            target.is_generated = source_metadata["is_generated"]
            target.parent_layer_id = source_metadata["parent_layer_id"]
            target.generation_depth = source_metadata["generation_depth"]
            target.generation_prompt = source_metadata["generation_prompt"]
            target.layer_mode = source_metadata["layer_mode"]
            target.looper.enabled = source_metadata["looper_enabled"]
            target.looper.sync_to_master = source_metadata["looper_sync_to_master"]
            target.looper.free_loop = source_metadata["looper_free_loop"]
            target.looper.start_offset = source_metadata["looper_start_offset"]
            target.looper.end_offset = source_metadata["looper_end_offset"]
            target.looper.fade_in_samples = source_metadata["looper_fade_in_samples"]
            target.looper.fade_out_samples = source_metadata["looper_fade_out_samples"]
            target.looper.reverse = source_metadata["looper_reverse"]
            target.looper.half_speed = source_metadata["looper_half_speed"]
            target.looper.double_speed = source_metadata["looper_double_speed"]
            target.reverse = source_metadata["reverse"]
            target.sampler.reverse = source_metadata["sampler_reverse"]
            target.speed = source_metadata["speed"]
            target.pitch_semitones = source_metadata["pitch_semitones"]
            target.filter_type = source_metadata["filter_type"]
            target.filter_cutoff_hz = source_metadata["filter_cutoff_hz"]
            target.reverb_amount = source_metadata["reverb_amount"]
            target.grain_density = source_metadata["grain_density"]
            target.grain_size_ms = source_metadata["grain_size_ms"]
            target.grain_jitter = source_metadata["grain_jitter"]
            target.inpaint_regions = source_metadata["inpaint_regions"]
            target.effects_applied = source_metadata["effects_applied"]
            self._clamp_loop_region(target)
            target.waveform_revision += 1
        return target

    def set_layer_mode(self, layer: Layer, mode: LayerMode) -> None:
        """set the behavior mode for a layer."""
        with layer._buf_lock:
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
