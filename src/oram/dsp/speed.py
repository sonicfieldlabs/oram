"""oram.dsp.speed — speed ratio resampling.

note: MVP speed change alters pitch (no time-stretching).
this is documented as a known limitation.
"""

from __future__ import annotations

import numpy as np
from scipy import signal


def change_speed(buffer: np.ndarray, ratio: float, sample_rate: int = 48000) -> np.ndarray:
    """change playback speed by resampling.

    ratio > 1.0 = faster (shorter duration, higher pitch)
    ratio < 1.0 = slower (longer duration, lower pitch)

    uses scipy.signal.resample for quality resampling.
    """
    if ratio == 1.0:
        return buffer.copy()

    ratio = max(0.25, min(4.0, ratio))

    original_length = buffer.shape[0]
    new_length = int(original_length / ratio)

    if new_length < 1:
        return buffer[:1].copy()

    if buffer.ndim == 1:
        return signal.resample(buffer, new_length).astype(np.float32)

    # resample each channel
    channels = buffer.shape[1]
    result = np.zeros((new_length, channels), dtype=np.float32)
    for ch in range(channels):
        result[:, ch] = signal.resample(buffer[:, ch], new_length).astype(np.float32)

    return result
