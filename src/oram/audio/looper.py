"""oram.audio.looper — per-layer loop behavior.

each layer can loop independently with its own start/end points,
fade edges, direction, and speed. loops are always block-based
to align with the engine's audio callback.
"""

from __future__ import annotations

import numpy as np

from oram.types import Layer, LooperParams


class LooperBehavior:
    """manages loop playback for a single layer."""

    def __init__(self, layer: Layer):
        self.layer = layer

    @property
    def params(self) -> LooperParams:
        return self.layer.looper

    @property
    def is_active(self) -> bool:
        return self.params.enabled and not self.layer.is_empty

    def get_loop_bounds(self) -> tuple[int, int]:
        """get the effective loop start and end in samples."""
        start = self.params.start_offset
        end = self.params.end_offset
        if end <= 0:
            end = self.layer.length_samples
        end = min(end, self.layer.length_samples)
        start = max(0, min(start, end - 1))
        return start, end

    def get_next_block(self, block_size: int) -> np.ndarray:
        """get the next block of audio from the looping layer.

        returns a stereo float32 array of shape (block_size, 2).
        handles wrapping, reverse, and speed changes.
        """
        if not self.is_active:
            return np.zeros((block_size, 2), dtype=np.float32)

        buf = self.layer.buffer
        start, end = self.get_loop_bounds()
        loop_len = end - start

        if loop_len <= 0:
            return np.zeros((block_size, 2), dtype=np.float32)

        # determine speed
        speed = 1.0
        if self.params.half_speed:
            speed = 0.5
        elif self.params.double_speed:
            speed = 2.0

        # generate output
        output = np.zeros((block_size, 2), dtype=np.float32)
        playhead = self.layer.playhead

        if speed == 1.0 and not self.params.reverse:
            # fast path: no resampling
            for i in range(block_size):
                pos = start + ((playhead - start) % loop_len)
                if 0 <= pos < buf.shape[0]:
                    output[i] = buf[pos]
                playhead += 1
        elif self.params.reverse:
            for i in range(block_size):
                pos = end - 1 - ((playhead - start) % loop_len)
                if 0 <= pos < buf.shape[0]:
                    output[i] = buf[pos]
                playhead += 1
        else:
            # resampled playback
            phase = float(playhead)
            for i in range(block_size):
                pos = int(phase) % loop_len + start
                if 0 <= pos < buf.shape[0]:
                    output[i] = buf[pos]
                phase += speed

            playhead = int(phase)

        # apply fades
        if self.params.fade_in_samples > 0 or self.params.fade_out_samples > 0:
            output = self._apply_fades(output, playhead - block_size, loop_len)

        self.layer.playhead = playhead
        return output

    def _apply_fades(self, block: np.ndarray, block_start: int, loop_len: int) -> np.ndarray:
        """apply crossfade at loop boundaries."""
        fade_in = self.params.fade_in_samples
        fade_out = self.params.fade_out_samples

        for i in range(len(block)):
            pos_in_loop = (block_start + i) % loop_len if loop_len > 0 else 0

            # fade in
            if fade_in > 0 and pos_in_loop < fade_in:
                block[i] *= pos_in_loop / fade_in

            # fade out
            if fade_out > 0 and pos_in_loop >= (loop_len - fade_out):
                remaining = loop_len - pos_in_loop
                block[i] *= remaining / fade_out

        return block

    def reset(self) -> None:
        """reset playhead to loop start."""
        start, _ = self.get_loop_bounds()
        self.layer.playhead = start

    def set_region(self, start: int, end: int) -> None:
        """set the loop region in samples."""
        self.params.start_offset = max(0, start)
        self.params.end_offset = max(0, end)

    def toggle(self) -> bool:
        """toggle looping on/off."""
        self.params.enabled = not self.params.enabled
        return self.params.enabled
