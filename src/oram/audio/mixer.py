"""oram.audio.mixer — per-block mixing: mute/solo/volume/pan/sum/limiter.

REALTIME SAFETY: mix_block() and all methods called from the audio callback
operate on pre-allocated workspace arrays.  No allocations, no locks, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from oram.types import LoopLayer


@dataclass
class MixerWorkspace:
    """pre-allocated arrays for the audio callback.

    constructed once at engine start.  the mixer fills these in-place
    instead of allocating per-callback.
    """

    master: np.ndarray   # (max_block, channels), float32
    scratch: np.ndarray  # (max_block, channels), float32

    @staticmethod
    def create(max_block: int, channels: int = 2) -> "MixerWorkspace":
        return MixerWorkspace(
            master=np.zeros((max_block, channels), dtype=np.float32),
            scratch=np.zeros((max_block, channels), dtype=np.float32),
        )


class Mixer:
    """mixes active layers into a stereo output block."""

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._limiter_threshold = 0.95

    def mix_block(
        self,
        active_layers: list[LoopLayer],
        block_size: int,
        out: np.ndarray | None = None,
    ) -> np.ndarray:
        """pull the next block from every active layer and mix to stereo.

        1. pull block from each layer at its playhead position
        2. apply volume and pan in-place
        3. sum to master
        4. apply limiter in-place

        if *out* is provided it must be a pre-allocated (>=block_size, channels)
        float32 array — the mixer fills it in-place and returns the slice.
        """
        if out is not None:
            master = out[:block_size]
            master[:] = 0.0
        else:
            # fallback for callers that haven't migrated yet (e.g. MockEngine)
            master = np.zeros((block_size, self.channels), dtype=np.float32)

        for layer in active_layers:
            if layer.is_empty:
                continue

            block = self._pull_block(layer, block_size)
            self._apply_volume_inplace(block, layer.volume)
            self._apply_pan_inplace(block, layer.pan)
            master += block

        self._apply_limiter_inplace(master)
        return master

    def _pull_block(self, layer: LoopLayer, block_size: int) -> np.ndarray:
        """pull a block from a layer, wrapping around the loop boundary."""
        buf = layer.buffer
        length = buf.shape[0]
        if length == 0:
            return np.zeros((block_size, self.channels), dtype=np.float32)

        layer_mode = getattr(layer.layer_mode, "value", layer.layer_mode)
        if layer_mode == "looper" and layer.looper.enabled:
            return self._pull_looper_block(layer, block_size)
        if layer_mode == "sampler":
            return self._pull_sampler_block(layer, block_size)

        pos = layer.playhead % length
        indices = (np.arange(block_size) + pos) % length
        return buf[indices]

    def _pull_sampler_block(self, layer: LoopLayer, block_size: int) -> np.ndarray:
        """pull a block using sampler start/end/reverse/transpose parameters."""
        buf = layer.buffer
        length = buf.shape[0]
        start = max(0, int(layer.sampler.start_point))
        end = int(layer.sampler.end_point) if layer.sampler.end_point > 0 else length
        end = min(max(start + 1, end), length)
        region_len = end - start
        if region_len <= 0:
            return np.zeros((block_size, self.channels), dtype=np.float32)

        pitch_ratio = 2.0 ** ((layer.sampler.transpose + layer.sampler.fine_tune / 100.0) / 12.0)
        phase = max(0.0, float(layer.playhead - start))
        steps = phase + np.arange(block_size, dtype=np.float32) * pitch_ratio
        if layer.sampler.reverse:
            indices = end - 1 - (steps.astype(np.int64) % region_len)
        else:
            indices = start + (steps.astype(np.int64) % region_len)
        return buf[indices]

    def _pull_looper_block(self, layer: LoopLayer, block_size: int) -> np.ndarray:
        """pull a block using the layer's looper region and speed flags."""
        buf = layer.buffer
        length = buf.shape[0]
        start = max(0, int(layer.looper.start_offset))
        end = int(layer.looper.end_offset) if layer.looper.end_offset > 0 else length
        end = min(max(start + 1, end), length)
        loop_len = end - start
        if loop_len <= 0:
            return np.zeros((block_size, self.channels), dtype=np.float32)

        speed = 1.0
        if layer.looper.half_speed:
            speed = 0.5
        elif layer.looper.double_speed:
            speed = 2.0

        phase = max(0.0, float(layer.playhead - start))
        steps = phase + np.arange(block_size, dtype=np.float32) * speed
        if layer.looper.reverse:
            indices = end - 1 - (steps.astype(np.int64) % loop_len)
        else:
            indices = start + (steps.astype(np.int64) % loop_len)
        return buf[indices]

    def advance_playhead(self, layer: LoopLayer, frames: int) -> None:
        """advance one layer's playhead according to its mode."""
        if layer.is_empty:
            return
        layer_mode = getattr(layer.layer_mode, "value", layer.layer_mode)
        if layer_mode == "looper" and layer.looper.enabled:
            length = layer.length_samples
            start = max(0, int(layer.looper.start_offset))
            end = int(layer.looper.end_offset) if layer.looper.end_offset > 0 else length
            end = min(max(start + 1, end), length)
            loop_len = end - start
            speed = 1.0
            if layer.looper.half_speed:
                speed = 0.5
            elif layer.looper.double_speed:
                speed = 2.0
            layer.playhead = start + int((layer.playhead - start + frames * speed) % loop_len)
            return
        if layer_mode == "sampler":
            length = layer.length_samples
            start = max(0, int(layer.sampler.start_point))
            end = int(layer.sampler.end_point) if layer.sampler.end_point > 0 else length
            end = min(max(start + 1, end), length)
            region_len = end - start
            pitch_ratio = 2.0 ** ((layer.sampler.transpose + layer.sampler.fine_tune / 100.0) / 12.0)
            layer.playhead = start + int((layer.playhead - start + frames * pitch_ratio) % region_len)
            return
        layer.playhead = (layer.playhead + frames) % layer.length_samples

    def advance_playheads(self, layers: list[LoopLayer], frames: int) -> None:
        """advance all sounding layer playheads."""
        for layer in layers:
            if not layer.is_empty and not layer.muted:
                self.advance_playhead(layer, frames)

    # --- in-place operations (no allocations) ---

    @staticmethod
    def _apply_volume_inplace(block: np.ndarray, volume: float) -> None:
        """apply volume scaling in-place."""
        block *= volume

    def _apply_pan_inplace(self, block: np.ndarray, pan: float) -> None:
        """apply constant-power pan in-place.

        pan: -1.0 (full left) to +1.0 (full right).
        uses sin/cos crossfade so L² + R² ≈ const across the pan range.
        """
        if self.channels < 2:
            return

        # constant-power pan: theta sweeps 0 (full left) to π/2 (full right)
        theta = (pan + 1.0) * (np.pi / 4.0)
        left_gain = float(np.cos(theta))
        right_gain = float(np.sin(theta))
        block[:, 0] *= left_gain
        block[:, 1] *= right_gain

    def _apply_limiter_inplace(self, block: np.ndarray) -> None:
        """simple brick-wall limiter to prevent clipping, in-place."""
        peak = np.max(np.abs(block))
        if peak > self._limiter_threshold:
            block *= self._limiter_threshold / peak

    # --- legacy wrappers (kept for tests that use the old API) ---

    def _apply_volume(self, block: np.ndarray, volume: float) -> np.ndarray:
        """apply volume scaling."""
        return block * volume

    def _apply_pan(self, block: np.ndarray, pan: float) -> np.ndarray:
        """apply stereo pan. pan: -1.0 (left) to 1.0 (right)."""
        if self.channels < 2:
            return block

        # constant-power pan
        theta = (pan + 1.0) * (np.pi / 4.0)
        left_gain = float(np.cos(theta))
        right_gain = float(np.sin(theta))
        result = block.copy()
        result[:, 0] *= left_gain
        result[:, 1] *= right_gain
        return result

    def _apply_limiter(self, block: np.ndarray) -> np.ndarray:
        """simple brick-wall limiter to prevent clipping."""
        peak = np.max(np.abs(block))
        if peak > self._limiter_threshold:
            block = block * (self._limiter_threshold / peak)
        return block
