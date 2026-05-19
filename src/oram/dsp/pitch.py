"""oram.dsp.pitch — pitch shift ±12 semitones.

uses resample_poly for anti-aliased resampling (§2.5).
this changes duration slightly.  a proper pitch shift preserving
duration can come later via rubberband or similar.
"""

from __future__ import annotations

from math import gcd

import numpy as np
from scipy.signal import resample_poly


def pitch_shift(
    buffer: np.ndarray,
    semitones: float,
    sample_rate: int = 48000,
) -> np.ndarray:
    """shift pitch by semitones using polyphase resampling.

    positive semitones = higher pitch
    negative semitones = lower pitch

    uses resample_poly for proper anti-aliasing at all ratios.
    note: this changes duration.
    """
    semitones = max(-12.0, min(12.0, semitones))

    if semitones == 0.0:
        return buffer.copy()

    # ratio: higher pitch = shorter buffer
    ratio = 2.0 ** (semitones / 12.0)

    # resample_poly needs integer up/down factors
    # approximate the ratio with reasonable precision
    precision = 1000
    up = int(precision)
    down = int(round(precision * ratio))
    d = gcd(up, down)
    up //= d
    down //= d

    # cap factors to avoid excessive computation
    if up > 100 or down > 100:
        factor = max(up, down) / 100
        up = max(1, int(up / factor))
        down = max(1, int(down / factor))

    if buffer.ndim == 1:
        return resample_poly(buffer, up, down).astype(np.float32)

    channels = buffer.shape[1]
    resampled_ch0 = resample_poly(buffer[:, 0], up, down).astype(np.float32)
    result = np.zeros((len(resampled_ch0), channels), dtype=np.float32)
    result[:, 0] = resampled_ch0
    for ch in range(1, channels):
        result[:, ch] = resample_poly(buffer[:, ch], up, down).astype(np.float32)

    return result
