"""oram.dsp.filter — lowpass, highpass, and bandpass filters.

'darker' -> lowpass
'thinner' -> highpass + slight gain reduction
'bandpass 800' -> band around a center frequency, optional resonance

without q: 4th-order Butterworth (flat, zero-phase offline).
with q: RBJ biquad so the cutoff can ring — the resonant sweep sound.
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


def _apply_sos(buffer: np.ndarray, sos: np.ndarray, gain: float = 1.0) -> np.ndarray:
    if buffer.ndim == 1:
        return sanitize_signal(_filter_channel(sos, buffer) * gain, peak=1.0)
    result = np.zeros_like(buffer)
    for ch in range(buffer.shape[1]):
        result[:, ch] = (_filter_channel(sos, buffer[:, ch]) * gain).astype(np.float32)
    return sanitize_signal(result, peak=1.0)


def _rbj_sos(kind: str, freq_hz: float, q: float, sample_rate: int) -> np.ndarray:
    """RBJ audio-EQ-cookbook biquad as one SOS section."""
    q = float(np.clip(q, 0.1, 12.0))
    w0 = 2.0 * np.pi * float(np.clip(freq_hz, 10.0, sample_rate * 0.49)) / sample_rate
    cos_w0, sin_w0 = np.cos(w0), np.sin(w0)
    alpha = sin_w0 / (2.0 * q)

    if kind == "low":
        b0 = (1 - cos_w0) / 2
        b1 = 1 - cos_w0
        b2 = (1 - cos_w0) / 2
    elif kind == "high":
        b0 = (1 + cos_w0) / 2
        b1 = -(1 + cos_w0)
        b2 = (1 + cos_w0) / 2
    else:  # bandpass, constant 0 dB peak gain
        b0 = alpha
        b1 = 0.0
        b2 = -alpha

    a0 = 1 + alpha
    a1 = -2 * cos_w0
    a2 = 1 - alpha
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])


def lowpass(
    buffer: np.ndarray,
    cutoff_hz: float = 2000.0,
    sample_rate: int = 44100,
    order: int = 4,
    q: float | None = None,
) -> np.ndarray:
    """apply a lowpass filter. makes things 'darker'.

    pass q for a resonant biquad instead of the flat Butterworth.
    """
    if q is not None:
        return _apply_sos(buffer, _rbj_sos("low", cutoff_hz, q, sample_rate))

    nyquist = sample_rate / 2
    cutoff_norm = max(0.001, min(cutoff_hz / nyquist, 0.99))
    sos = butter(order, cutoff_norm, btype="low", output="sos")
    return _apply_sos(buffer, sos)


def highpass(
    buffer: np.ndarray,
    cutoff_hz: float = 4000.0,
    sample_rate: int = 44100,
    order: int = 4,
    gain_reduction: float = 0.85,
    q: float | None = None,
) -> np.ndarray:
    """apply a highpass filter. makes things 'thinner'."""
    if q is not None:
        return _apply_sos(buffer, _rbj_sos("high", cutoff_hz, q, sample_rate), gain_reduction)

    nyquist = sample_rate / 2
    cutoff_norm = max(0.001, min(cutoff_hz / nyquist, 0.99))
    sos = butter(order, cutoff_norm, btype="high", output="sos")
    return _apply_sos(buffer, sos, gain_reduction)


def bandpass(
    buffer: np.ndarray,
    center_hz: float = 800.0,
    q: float = 1.2,
    sample_rate: int = 44100,
) -> np.ndarray:
    """isolate a band around center_hz; higher q = narrower and more vocal."""
    return _apply_sos(buffer, _rbj_sos("band", center_hz, q, sample_rate))
