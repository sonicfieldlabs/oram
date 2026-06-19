"""oram.dsp.filter — lowpass and highpass filters.

'darker' -> lowpass
'thinner' -> highpass + slight gain reduction
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt, sosfiltfilt

from oram.dsp.safety import sanitize_signal


def _filter_channel(sos: np.ndarray, channel: np.ndarray) -> np.ndarray:
    """Apply an offline SOS filter without zero-state startup thumps."""
    if channel.shape[0] < max(16, sos.shape[0] * 9):
        return sosfilt(sos, channel).astype(np.float32)
    try:
        return sosfiltfilt(sos, channel).astype(np.float32)
    except ValueError:
        return sosfilt(sos, channel).astype(np.float32)


def lowpass(
    buffer: np.ndarray,
    cutoff_hz: float = 2000.0,
    sample_rate: int = 44100,
    order: int = 4,
) -> np.ndarray:
    """apply a lowpass filter. makes things 'darker'."""
    nyquist = sample_rate / 2
    cutoff_norm = max(0.001, min(cutoff_hz / nyquist, 0.99))

    sos = butter(order, cutoff_norm, btype="low", output="sos")

    if buffer.ndim == 1:
        return sanitize_signal(_filter_channel(sos, buffer))

    result = np.zeros_like(buffer)
    for ch in range(buffer.shape[1]):
        result[:, ch] = _filter_channel(sos, buffer[:, ch])
    return sanitize_signal(result)


def highpass(
    buffer: np.ndarray,
    cutoff_hz: float = 4000.0,
    sample_rate: int = 44100,
    order: int = 4,
    gain_reduction: float = 0.85,
) -> np.ndarray:
    """apply a highpass filter. makes things 'thinner'."""
    nyquist = sample_rate / 2
    cutoff_norm = max(0.001, min(cutoff_hz / nyquist, 0.99))

    sos = butter(order, cutoff_norm, btype="high", output="sos")

    if buffer.ndim == 1:
        return sanitize_signal(_filter_channel(sos, buffer) * gain_reduction)

    result = np.zeros_like(buffer)
    for ch in range(buffer.shape[1]):
        result[:, ch] = (_filter_channel(sos, buffer[:, ch]) * gain_reduction).astype(np.float32)
    return sanitize_signal(result)
