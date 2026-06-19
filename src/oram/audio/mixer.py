"""oram.audio.mixer — per-block mixing: mute/solo/volume/pan/sum/limiter.

REALTIME SAFETY: the callback uses mix_block_and_advance() with a pre-allocated
workspace.  It takes short per-layer locks only while reading buffer metadata and
advancing playheads, so DSP workers cannot swap a resized buffer mid-block.
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
    scratch_next: np.ndarray  # (max_block, channels), float32
    frame_offsets: np.ndarray  # (max_block,), float32
    positions: np.ndarray  # (max_block,), float32
    fractions: np.ndarray  # (max_block,), float32
    gains: np.ndarray  # (max_block,), float32
    indices: np.ndarray  # (max_block,), intp
    next_indices: np.ndarray  # (max_block,), intp

    @staticmethod
    def create(max_block: int, channels: int = 2) -> "MixerWorkspace":
        return MixerWorkspace(
            master=np.zeros((max_block, channels), dtype=np.float32),
            scratch=np.zeros((max_block, channels), dtype=np.float32),
            scratch_next=np.zeros((max_block, channels), dtype=np.float32),
            frame_offsets=np.arange(max_block, dtype=np.float32),
            positions=np.zeros(max_block, dtype=np.float32),
            fractions=np.zeros(max_block, dtype=np.float32),
            gains=np.zeros(max_block, dtype=np.float32),
            indices=np.zeros(max_block, dtype=np.intp),
            next_indices=np.zeros(max_block, dtype=np.intp),
        )


class Mixer:
    """mixes active layers into a stereo output block."""

    def __init__(self, sample_rate: int = 44100, channels: int = 2):
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

    def mix_block_and_advance(
        self,
        layers: list[LoopLayer],
        block_size: int,
        out: np.ndarray | None = None,
        workspace: MixerWorkspace | None = None,
    ) -> np.ndarray:
        """Mix audible layers and advance playheads from one coherent snapshot."""
        if workspace is not None:
            master = workspace.master[:block_size]
            master[:] = 0.0
        elif out is not None:
            master = out[:block_size]
            master[:] = 0.0
        else:
            master = np.zeros((block_size, self.channels), dtype=np.float32)

        any_solo = self._has_active_solo(layers)
        for layer in layers:
            pulled = self._pull_and_advance_if_audible(
                layer,
                block_size,
                any_solo,
                workspace,
            )
            if pulled is None:
                continue

            block, volume, pan = pulled
            self._apply_volume_inplace(block, volume)
            self._apply_pan_inplace(block, pan)
            master += block

        self._apply_limiter_inplace(master)
        return master

    @staticmethod
    def _has_active_solo(layers: list[LoopLayer]) -> bool:
        for layer in layers:
            lock = getattr(layer, "_buf_lock", None)
            if lock is None:
                if layer.solo and not layer.is_empty:
                    return True
                continue
            with lock:
                if layer.solo and not layer.is_empty:
                    return True
        return False

    def _pull_and_advance_if_audible(
        self,
        layer: LoopLayer,
        block_size: int,
        any_solo: bool,
        workspace: MixerWorkspace | None = None,
    ) -> tuple[np.ndarray, float, float] | None:
        lock = getattr(layer, "_buf_lock", None)
        if lock is None:
            return self._pull_and_advance_if_audible_unlocked(
                layer,
                block_size,
                any_solo,
                workspace,
            )
        with lock:
            return self._pull_and_advance_if_audible_unlocked(
                layer,
                block_size,
                any_solo,
                workspace,
            )

    def _pull_and_advance_if_audible_unlocked(
        self,
        layer: LoopLayer,
        block_size: int,
        any_solo: bool,
        workspace: MixerWorkspace | None = None,
    ) -> tuple[np.ndarray, float, float] | None:
        if layer.is_empty:
            layer.playhead = 0
            return None
        if any_solo:
            if not layer.solo:
                return None
        elif layer.muted:
            return None

        block = self._pull_block(layer, block_size, workspace=workspace)
        volume = float(layer.volume)
        pan = float(layer.pan)
        self.advance_playhead(layer, block_size)
        return block, volume, pan

    def _pull_block(
        self,
        layer: LoopLayer,
        block_size: int,
        workspace: MixerWorkspace | None = None,
    ) -> np.ndarray:
        """pull a block from a layer, wrapping around the loop boundary."""
        buf = layer.buffer
        length = buf.shape[0]
        if length == 0:
            return np.zeros((block_size, self.channels), dtype=np.float32)

        layer_mode = getattr(layer.layer_mode, "value", layer.layer_mode)
        if layer_mode == "looper" and layer.looper.enabled:
            return self._pull_looper_block(layer, block_size, workspace=workspace)
        if layer_mode == "sampler":
            return self._pull_sampler_block(layer, block_size, workspace=workspace)

        pos = layer.playhead % length
        speed = max(0.01, float(getattr(layer, "speed", 1.0) or 1.0))
        if workspace is not None:
            return self._resample_region_into(
                buf,
                start=0,
                end=length,
                phase=float(pos),
                speed=speed,
                block_size=block_size,
                reverse=bool(getattr(layer, "reverse", False)),
                workspace=workspace,
                out=workspace.scratch[:block_size],
            )

        steps = pos + np.arange(block_size, dtype=np.float32) * speed
        if getattr(layer, "reverse", False):
            indices = length - 1 - (steps.astype(np.int64) % length)
        else:
            indices = steps.astype(np.int64) % length
        return buf[indices]

    def _pull_sampler_block(
        self,
        layer: LoopLayer,
        block_size: int,
        workspace: MixerWorkspace | None = None,
    ) -> np.ndarray:
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
        if workspace is not None:
            return self._resample_region_into(
                buf,
                start=start,
                end=end,
                phase=phase,
                speed=pitch_ratio,
                block_size=block_size,
                reverse=bool(layer.sampler.reverse),
                workspace=workspace,
                out=workspace.scratch[:block_size],
            )

        steps = phase + np.arange(block_size, dtype=np.float32) * pitch_ratio
        if layer.sampler.reverse:
            indices = end - 1 - (steps.astype(np.int64) % region_len)
        else:
            indices = start + (steps.astype(np.int64) % region_len)
        return buf[indices]

    def _pull_looper_block(
        self,
        layer: LoopLayer,
        block_size: int,
        workspace: MixerWorkspace | None = None,
    ) -> np.ndarray:
        """pull a block using the layer's looper region and speed flags."""
        buf = layer.buffer
        length = buf.shape[0]
        start = max(0, int(layer.looper.start_offset))
        end = int(layer.looper.end_offset) if layer.looper.end_offset > 0 else length
        end = min(max(start + 1, end), length)
        loop_len = end - start
        if loop_len <= 0:
            return np.zeros((block_size, self.channels), dtype=np.float32)

        speed = max(0.01, float(getattr(layer, "speed", 1.0) or 1.0))
        if layer.looper.half_speed:
            speed *= 0.5
        elif layer.looper.double_speed:
            speed *= 2.0

        phase = max(0.0, float(layer.playhead - start))
        if workspace is not None:
            block = self._resample_region_into(
                buf,
                start=start,
                end=end,
                phase=phase,
                speed=speed,
                block_size=block_size,
                reverse=bool(layer.looper.reverse),
                workspace=workspace,
                out=workspace.scratch[:block_size],
            )
            positions = workspace.positions[:block_size]
            self._apply_loop_fades_inplace(
                block,
                positions,
                loop_len,
                layer.looper.fade_in_samples,
                layer.looper.fade_out_samples,
                workspace=workspace,
            )
            return block

        steps = phase + np.arange(block_size, dtype=np.float32) * speed
        if layer.looper.reverse:
            indices = end - 1 - (steps.astype(np.int64) % loop_len)
        else:
            indices = start + (steps.astype(np.int64) % loop_len)
        block = buf[indices].copy()
        self._apply_loop_fades_inplace(
            block,
            steps,
            loop_len,
            layer.looper.fade_in_samples,
            layer.looper.fade_out_samples,
        )
        return block

    def _resample_region_into(
        self,
        buf: np.ndarray,
        *,
        start: int,
        end: int,
        phase: float,
        speed: float,
        block_size: int,
        reverse: bool,
        workspace: MixerWorkspace,
        out: np.ndarray,
    ) -> np.ndarray:
        """Pull a region into *out* with linear interpolation and no heap churn."""
        region_len = max(1, int(end) - int(start))
        positions = workspace.positions[:block_size]
        fractions = workspace.fractions[:block_size]
        gains = workspace.gains[:block_size]
        indices = workspace.indices[:block_size]
        next_indices = workspace.next_indices[:block_size]
        offsets = workspace.frame_offsets[:block_size]
        scratch_next = workspace.scratch_next[:block_size]

        np.multiply(offsets, np.float32(speed), out=positions)
        positions += np.float32(phase)
        np.floor(positions, out=fractions)
        np.subtract(positions, fractions, out=positions)
        indices[:] = fractions
        np.remainder(indices, region_len, out=indices)
        np.add(indices, 1, out=next_indices)
        np.remainder(next_indices, region_len, out=next_indices)

        if reverse:
            np.subtract(end - 1, indices, out=indices)
            np.subtract(end - 1, next_indices, out=next_indices)
        else:
            indices += start
            next_indices += start

        np.take(buf, indices, axis=0, out=out)
        np.take(buf, next_indices, axis=0, out=scratch_next)
        fractions[:] = positions
        np.subtract(1.0, fractions, out=gains)
        out *= gains[:, np.newaxis]
        scratch_next *= fractions[:, np.newaxis]
        out += scratch_next

        np.multiply(offsets, np.float32(speed), out=positions)
        positions += np.float32(phase)
        return out

    @staticmethod
    def _apply_loop_fades_inplace(
        block: np.ndarray,
        positions: np.ndarray,
        loop_len: int,
        fade_in_samples: int,
        fade_out_samples: int,
        workspace: MixerWorkspace | None = None,
    ) -> None:
        """apply non-destructive edge fades to a pulled looper block."""
        if loop_len <= 1:
            return
        fade_in = max(0, min(int(fade_in_samples), loop_len - 1))
        fade_out = max(0, min(int(fade_out_samples), loop_len - 1))
        if fade_in == 0 and fade_out == 0:
            return

        if workspace is not None:
            pos = workspace.positions[:block.shape[0]]
            gain = workspace.gains[:block.shape[0]]
            fade = workspace.fractions[:block.shape[0]]
            np.remainder(positions, loop_len, out=pos)
            gain[:] = 1.0
            if fade_in > 0:
                np.divide(pos, float(fade_in), out=fade)
                np.clip(fade, 0.0, 1.0, out=fade)
                np.minimum(gain, fade, out=gain)
            if fade_out > 0:
                np.subtract(float(loop_len), pos, out=fade)
                np.divide(fade, float(fade_out), out=fade)
                np.clip(fade, 0.0, 1.0, out=fade)
                np.minimum(gain, fade, out=gain)
        else:
            pos = np.mod(positions, loop_len).astype(np.float32, copy=False)
            gain = np.ones(block.shape[0], dtype=np.float32)
            if fade_in > 0:
                gain = np.minimum(gain, np.clip(pos / float(fade_in), 0.0, 1.0))
            if fade_out > 0:
                remaining = loop_len - pos
                gain = np.minimum(gain, np.clip(remaining / float(fade_out), 0.0, 1.0))
        if block.ndim == 1:
            block *= gain
        else:
            block *= gain[:, np.newaxis]

    def advance_playhead(self, layer: LoopLayer, frames: int) -> None:
        """advance one layer's playhead according to its mode."""
        if layer.is_empty:
            layer.playhead = 0
            return
        length = layer.length_samples
        if length <= 0:
            layer.playhead = 0
            return
        layer_mode = getattr(layer.layer_mode, "value", layer.layer_mode)
        if layer_mode == "looper" and layer.looper.enabled:
            start = max(0, int(layer.looper.start_offset))
            end = int(layer.looper.end_offset) if layer.looper.end_offset > 0 else length
            end = min(max(start + 1, end), length)
            loop_len = end - start
            if loop_len <= 0:
                layer.playhead = 0
                return
            speed = max(0.01, float(getattr(layer, "speed", 1.0) or 1.0))
            if layer.looper.half_speed:
                speed *= 0.5
            elif layer.looper.double_speed:
                speed *= 2.0
            layer.playhead = start + int((layer.playhead - start + frames * speed) % loop_len)
            return
        if layer_mode == "sampler":
            length = layer.length_samples
            start = max(0, int(layer.sampler.start_point))
            end = int(layer.sampler.end_point) if layer.sampler.end_point > 0 else length
            end = min(max(start + 1, end), length)
            region_len = end - start
            if region_len <= 0:
                layer.playhead = 0
                return
            pitch_ratio = 2.0 ** ((layer.sampler.transpose + layer.sampler.fine_tune / 100.0) / 12.0)
            layer.playhead = start + int((layer.playhead - start + frames * pitch_ratio) % region_len)
            return
        speed = max(0.01, float(getattr(layer, "speed", 1.0) or 1.0))
        layer.playhead = int(layer.playhead + frames * speed) % length

    def advance_playheads(self, layers: list[LoopLayer], frames: int) -> None:
        """advance all sounding layer playheads."""
        any_solo = any(l.solo for l in layers)
        for layer in layers:
            if layer.is_empty:
                continue
            if any_solo:
                # when solo is active, advance solo'd layers regardless of mute
                if layer.solo:
                    self.advance_playhead(layer, frames)
            elif not layer.muted:
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
