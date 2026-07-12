"""oram.audio.mixer — per-block mixing: mute/solo/volume/pan/sum/limiter.

REALTIME SAFETY: the callback uses mix_block_and_advance() with a pre-allocated
workspace.  It takes short per-layer locks only while reading buffer metadata and
advancing playheads, so DSP workers cannot swap a resized buffer mid-block.

v0.4 engine quality:
- playheads accumulate fractionally (layer._phase), so non-integer speeds no
  longer truncate sub-sample position every block (pitch drift + block-rate
  zipper are gone)
- region reads interpolate with 4-point Catmull-Rom instead of linear
- the output limiter ramps its gain across the block instead of stepping it,
  so limiting no longer clicks at block boundaries
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
    scratch_prev: np.ndarray   # (max_block, channels), float32 — p0
    scratch_next: np.ndarray   # (max_block, channels), float32 — p2
    scratch_next2: np.ndarray  # (max_block, channels), float32 — p3
    poly_a: np.ndarray   # (max_block, channels), float32 — cubic temp
    poly_b: np.ndarray   # (max_block, channels), float32 — cubic temp
    frame_offsets: np.ndarray  # (max_block,), float32
    positions: np.ndarray  # (max_block,), float32
    fractions: np.ndarray  # (max_block,), float32
    gains: np.ndarray  # (max_block,), float32
    indices: np.ndarray  # (max_block,), intp
    prev_indices: np.ndarray   # (max_block,), intp
    next_indices: np.ndarray   # (max_block,), intp
    next2_indices: np.ndarray  # (max_block,), intp

    @staticmethod
    def create(max_block: int, channels: int = 2) -> "MixerWorkspace":
        def _f2() -> np.ndarray:
            return np.zeros((max_block, channels), dtype=np.float32)

        def _idx() -> np.ndarray:
            return np.zeros(max_block, dtype=np.intp)

        return MixerWorkspace(
            master=_f2(),
            scratch=_f2(),
            scratch_prev=_f2(),
            scratch_next=_f2(),
            scratch_next2=_f2(),
            poly_a=_f2(),
            poly_b=_f2(),
            frame_offsets=np.arange(max_block, dtype=np.float32),
            positions=np.zeros(max_block, dtype=np.float32),
            fractions=np.zeros(max_block, dtype=np.float32),
            gains=np.zeros(max_block, dtype=np.float32),
            indices=_idx(),
            prev_indices=_idx(),
            next_indices=_idx(),
            next2_indices=_idx(),
        )


class Mixer:
    """mixes active layers into a stereo output block."""

    def __init__(self, sample_rate: int = 44100, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._limiter_threshold = 0.95
        self._limiter_gain = 1.0  # smoothed gain state across blocks

    # --- fractional playhead helpers ---

    @staticmethod
    def _effective_position(layer: LoopLayer) -> float:
        """current playback position as a float, resyncing after external
        playhead writes (assign/clear/silence set playhead directly)."""
        phase = float(getattr(layer, "_phase", 0.0))
        if int(phase) != int(layer.playhead):
            phase = float(layer.playhead)
            layer._phase = phase
        return phase

    @staticmethod
    def _store_position(layer: LoopLayer, position: float) -> None:
        layer._phase = float(position)
        layer.playhead = int(position)

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

        self._apply_limiter_inplace(master, workspace)
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
            self._store_position(layer, 0.0)
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

        pos = self._effective_position(layer) % length
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
        phase = max(0.0, self._effective_position(layer) - start)
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

        phase = max(0.0, self._effective_position(layer) - start)
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
        """Pull a region into *out* with Catmull-Rom interpolation, no heap churn.

        neighbors are computed in region (phase) space and then mapped, so
        reverse playback interpolates in the correct direction.
        """
        region_len = max(1, int(end) - int(start))
        positions = workspace.positions[:block_size]
        fractions = workspace.fractions[:block_size]
        indices = workspace.indices[:block_size]
        prev_indices = workspace.prev_indices[:block_size]
        next_indices = workspace.next_indices[:block_size]
        next2_indices = workspace.next2_indices[:block_size]
        offsets = workspace.frame_offsets[:block_size]
        p0 = workspace.scratch_prev[:block_size]
        p2 = workspace.scratch_next[:block_size]
        p3 = workspace.scratch_next2[:block_size]
        poly_a = workspace.poly_a[:block_size]
        poly_b = workspace.poly_b[:block_size]

        # positions = phase + i*speed; split into integer index + fraction
        np.multiply(offsets, np.float32(speed), out=positions)
        positions += np.float32(phase)
        np.floor(positions, out=fractions)
        np.subtract(positions, fractions, out=positions)  # positions = frac
        indices[:] = fractions
        np.remainder(indices, region_len, out=indices)

        np.subtract(indices, 1, out=prev_indices)
        np.remainder(prev_indices, region_len, out=prev_indices)
        np.add(indices, 1, out=next_indices)
        np.remainder(next_indices, region_len, out=next_indices)
        np.add(indices, 2, out=next2_indices)
        np.remainder(next2_indices, region_len, out=next2_indices)

        if reverse:
            np.subtract(end - 1, indices, out=indices)
            np.subtract(end - 1, prev_indices, out=prev_indices)
            np.subtract(end - 1, next_indices, out=next_indices)
            np.subtract(end - 1, next2_indices, out=next2_indices)
        else:
            indices += start
            prev_indices += start
            next_indices += start
            next2_indices += start

        np.take(buf, prev_indices, axis=0, out=p0)
        np.take(buf, indices, axis=0, out=out)      # out = p1
        np.take(buf, next_indices, axis=0, out=p2)
        np.take(buf, next2_indices, axis=0, out=p3)

        # Catmull-Rom: p1 + 0.5·t·(c1 + t·(c2 + t·c3))
        #   c1 = p2 − p0
        #   c2 = 2p0 − 5p1 + 4p2 − p3
        #   c3 = 3(p1 − p2) + p3 − p0
        t = positions[:, np.newaxis]

        np.subtract(out, p2, out=poly_a)      # p1 − p2
        poly_a *= np.float32(3.0)
        poly_a += p3
        poly_a -= p0                          # c3

        np.multiply(p0, np.float32(2.0), out=poly_b)
        poly_b -= out
        poly_b -= out
        poly_b -= out
        poly_b -= out
        poly_b -= out                         # 2p0 − 5p1
        poly_b += p2
        poly_b += p2
        poly_b += p2
        poly_b += p2                          # + 4p2
        poly_b -= p3                          # c2

        np.subtract(p2, p0, out=p3)           # reuse p3 as c1

        poly_a *= t
        poly_a += poly_b
        poly_a *= t
        poly_a += p3
        poly_a *= t
        poly_a *= np.float32(0.5)
        out += poly_a

        # restore raw positions (phase + i·speed) for the loop-fade pass
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
        """advance one layer's playhead according to its mode.

        positions accumulate fractionally in layer._phase; layer.playhead
        stays the public integer view.
        """
        if layer.is_empty:
            self._store_position(layer, 0.0)
            return
        length = layer.length_samples
        if length <= 0:
            self._store_position(layer, 0.0)
            return
        position = self._effective_position(layer)
        layer_mode = getattr(layer.layer_mode, "value", layer.layer_mode)
        if layer_mode == "looper" and layer.looper.enabled:
            start = max(0, int(layer.looper.start_offset))
            end = int(layer.looper.end_offset) if layer.looper.end_offset > 0 else length
            end = min(max(start + 1, end), length)
            loop_len = end - start
            if loop_len <= 0:
                self._store_position(layer, 0.0)
                return
            speed = max(0.01, float(getattr(layer, "speed", 1.0) or 1.0))
            if layer.looper.half_speed:
                speed *= 0.5
            elif layer.looper.double_speed:
                speed *= 2.0
            self._store_position(layer, start + ((position - start + frames * speed) % loop_len))
            return
        if layer_mode == "sampler":
            start = max(0, int(layer.sampler.start_point))
            end = int(layer.sampler.end_point) if layer.sampler.end_point > 0 else length
            end = min(max(start + 1, end), length)
            region_len = end - start
            if region_len <= 0:
                self._store_position(layer, 0.0)
                return
            pitch_ratio = 2.0 ** ((layer.sampler.transpose + layer.sampler.fine_tune / 100.0) / 12.0)
            self._store_position(layer, start + ((position - start + frames * pitch_ratio) % region_len))
            return
        speed = max(0.01, float(getattr(layer, "speed", 1.0) or 1.0))
        self._store_position(layer, (position + frames * speed) % length)

    def advance_playheads(self, layers: list[LoopLayer], frames: int) -> None:
        """advance all sounding layer playheads."""
        any_solo = any(l.solo and not l.is_empty for l in layers)
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

    def _apply_limiter_inplace(
        self,
        block: np.ndarray,
        workspace: MixerWorkspace | None = None,
    ) -> None:
        """brick-wall limiter with a per-block gain ramp, in-place.

        the gain moves linearly from its previous value to the new target
        across the block, so limiting engages/releases without the
        block-boundary steps the old instant rescale produced.
        """
        n = block.shape[0]
        if n == 0:
            return
        peak = float(np.max(np.abs(block)))
        target = self._limiter_gain
        if peak * target > self._limiter_threshold:
            target = self._limiter_threshold / peak
        elif peak <= self._limiter_threshold:
            target = min(1.0, self._limiter_gain + 0.05)  # gentle release

        previous = self._limiter_gain
        if previous == target == 1.0:
            return

        if workspace is not None:
            ramp = workspace.gains[:n]
            np.multiply(
                workspace.frame_offsets[:n],
                np.float32((target - previous) / n),
                out=ramp,
            )
            ramp += np.float32(previous)
        else:
            ramp = np.linspace(previous, target, n, dtype=np.float32)

        if block.ndim == 1:
            block *= ramp
        else:
            block *= ramp[:, np.newaxis]
        np.clip(block, -1.0, 1.0, out=block)
        self._limiter_gain = target

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
