"""oram.dsp.pitch — pitch shift ±12 semitones.

default mode preserves duration (phase-vocoder stretch + polyphase
resample), so a pitched loop keeps its loop length.  the legacy
tape-style mode (duration changes with pitch) stays available via
`preserve_duration=False`.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
from scipy.signal import resample_poly

from oram.dsp.safety import sanitize_signal


def _ratio_factors(ratio: float) -> tuple[int, int]:
    """integer up/down factors for a resample ratio, accurate to <1 cent."""
    fraction = Fraction(1.0 / ratio).limit_denominator(999)
    return max(1, fraction.numerator), max(1, fraction.denominator)


def _resample_by_ratio(buffer: np.ndarray, ratio: float) -> np.ndarray:
    up, down = _ratio_factors(ratio)
    if buffer.ndim == 1:
        return resample_poly(buffer, up, down).astype(np.float32)
    return resample_poly(buffer, up, down, axis=0).astype(np.float32)


def pitch_shift(
    buffer: np.ndarray,
    semitones: float,
    sample_rate: int = 44100,
    preserve_duration: bool = True,
) -> np.ndarray:
    """shift pitch by semitones.

    positive semitones = higher pitch, negative = lower.

    preserve_duration=True (default): phase-vocoder stretch + anti-aliased
    resample — the buffer keeps its length, so loops stay in time.
    preserve_duration=False: legacy varispeed behavior (duration changes).
    """
    semitones = max(-12.0, min(12.0, semitones))

    if semitones == 0.0:
        return buffer.copy()

    # ratio: higher pitch = shorter buffer after resampling
    ratio = 2.0 ** (semitones / 12.0)

    if not preserve_duration:
        return sanitize_signal(_resample_by_ratio(buffer, ratio), peak=1.0)

    from oram.dsp.stretch import time_stretch

    original_length = buffer.shape[0]
    # stretch by the pitch ratio, then resample back to the original length:
    # net effect is a transposition at constant duration.
    stretched = time_stretch(buffer, ratio, sample_rate=sample_rate)
    shifted = _resample_by_ratio(stretched, ratio)

    if shifted.shape[0] > original_length:
        shifted = shifted[:original_length]
    elif shifted.shape[0] < original_length:
        pad = ((0, original_length - shifted.shape[0]),) + ((0, 0),) * (buffer.ndim - 1)
        shifted = np.pad(shifted, pad)

    return sanitize_signal(shifted, peak=1.0)
