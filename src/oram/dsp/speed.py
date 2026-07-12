"""oram.dsp.speed — speed ratio resampling.

tape-style varispeed: changing speed changes pitch.  every ratio now goes
through anti-aliased polyphase resampling — the old ×2 fast path decimated
without a lowpass and audibly aliased.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
from scipy.signal import resample_poly

from oram.dsp.safety import sanitize_signal


def _fit_length(buffer: np.ndarray, length: int) -> np.ndarray:
    if buffer.shape[0] == length:
        return buffer
    if buffer.shape[0] > length:
        return buffer[:length]
    pad = length - buffer.shape[0]
    if buffer.shape[0] == 0:
        pad_width = (0, pad) if buffer.ndim == 1 else ((0, pad), (0, 0))
        return np.pad(buffer, pad_width, mode="constant")
    pad_width = (0, pad) if buffer.ndim == 1 else ((0, pad), (0, 0))
    return np.pad(buffer, pad_width, mode="edge")


def change_speed(buffer: np.ndarray, ratio: float, sample_rate: int = 44100) -> np.ndarray:
    """change playback speed by resampling.

    ratio > 1.0 = faster (shorter duration, higher pitch)
    ratio < 1.0 = slower (longer duration, lower pitch)

    uses polyphase resampling for anti-aliasing at every ratio.
    """
    if ratio == 1.0:
        return buffer.copy()

    ratio = max(0.25, min(4.0, ratio))

    original_length = buffer.shape[0]
    new_length = int(round(original_length / ratio))

    if new_length < 1:
        return buffer[:1].copy()

    fraction = Fraction(1.0 / ratio).limit_denominator(512)
    result = resample_poly(buffer, fraction.numerator, fraction.denominator, axis=0).astype(np.float32)
    return sanitize_signal(_fit_length(result, new_length), peak=1.0)
