"""oram.dsp.filter — lowpass and highpass filters.

'darker' -> lowpass
'thinner' -> highpass + slight gain reduction
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt


def lowpass(
    buffer: np.ndarray,
    cutoff_hz: float = 2000.0,
    sample_rate: int = 48000,
    order: int = 4,
) -> np.ndarray:
    """apply a lowpass filter. makes things 'darker'."""
    nyquist = sample_rate / 2
    cutoff_norm = min(cutoff_hz / nyquist, 0.99)

    sos = butter(order, cutoff_norm, btype="low", output="sos")

    if buffer.ndim == 1:
        return sosfilt(sos, buffer).astype(np.float32)

    result = np.zeros_like(buffer)
    for ch in range(buffer.shape[1]):
        result[:, ch] = sosfilt(sos, buffer[:, ch]).astype(np.float32)
    return result


def highpass(
    buffer: np.ndarray,
    cutoff_hz: float = 4000.0,
    sample_rate: int = 48000,
    order: int = 4,
    gain_reduction: float = 0.85,
) -> np.ndarray:
    """apply a highpass filter. makes things 'thinner'."""
    nyquist = sample_rate / 2
    cutoff_norm = min(cutoff_hz / nyquist, 0.99)

    sos = butter(order, cutoff_norm, btype="high", output="sos")

    if buffer.ndim == 1:
        return (sosfilt(sos, buffer) * gain_reduction).astype(np.float32)

    result = np.zeros_like(buffer)
    for ch in range(buffer.shape[1]):
        result[:, ch] = (sosfilt(sos, buffer[:, ch]) * gain_reduction).astype(np.float32)
    return result
