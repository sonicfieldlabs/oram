"""oram.dsp.stutter — slice-repeat glitching.

the buffer is cut into a slice grid; some slices grab the material of the
slice before them and repeat it with micro-fades.  loop length never
changes — the glitch stays in time.
"""

from __future__ import annotations

import numpy as np

from oram.dsp.safety import sanitize_signal


def stutter(
    buffer: np.ndarray,
    slice_ms: float | None = None,
    repeats: int = 4,
    prob: float = 0.5,
    sample_rate: int = 44100,
    seed: int | None = None,
) -> np.ndarray:
    """repeat slices of the buffer in place.

    slice_ms: slice length (default: 1/16 of the buffer, clamped 60–250 ms)
    repeats: how many grid cells a stutter occupies once triggered
    prob: chance a grid cell starts a stutter (0–1)
    """
    audio = sanitize_signal(buffer)
    n = audio.shape[0]
    if n == 0:
        return audio.copy()

    if slice_ms is None:
        slice_ms = float(np.clip((n / sample_rate) * 1000.0 / 16.0, 60.0, 250.0))
    slice_len = max(32, int(slice_ms * sample_rate / 1000.0))
    n_slices = max(1, n // slice_len)
    if n_slices < 2:
        return audio.copy()

    repeats = int(np.clip(repeats, 2, 16))
    prob = float(np.clip(prob, 0.0, 1.0))
    rng = np.random.default_rng(seed)

    out = audio.copy()
    fade_len = min(96, slice_len // 8)
    fade_in = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
    if audio.ndim > 1:
        fade_in = fade_in[:, np.newaxis]
    fade_out = fade_in[::-1]

    i = 1
    while i < n_slices:
        if rng.random() < prob:
            src_start = (i - 1) * slice_len
            source = audio[src_start:src_start + slice_len]
            span = min(repeats, n_slices - i)
            for r in range(span):
                dst = (i + r) * slice_len
                seg = out[dst:dst + slice_len]
                m = min(seg.shape[0], source.shape[0])
                seg[:m] = source[:m]
                if m > 2 * fade_len:
                    seg[:fade_len] *= fade_in
                    seg[m - fade_len:m] *= fade_out
            i += span
        else:
            i += 1

    return sanitize_signal(out)
