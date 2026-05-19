"""oram.dsp.reverb — simple schroeder-style reverb.

spatial command mappings:
- 'far away' -> lower volume, more reverb, slight lowpass
- 'small room' -> short decay
- 'wash it in reverb' -> higher wet mix
"""

from __future__ import annotations

import numpy as np


def _comb_filter(
    signal_in: np.ndarray, delay_samples: int, feedback: float
) -> np.ndarray:
    """single comb filter."""
    output = np.zeros(len(signal_in) + delay_samples, dtype=np.float32)
    output[: len(signal_in)] = signal_in.copy()

    for i in range(delay_samples, len(output)):
        output[i] += output[i - delay_samples] * feedback

    return output[: len(signal_in)]


def _allpass_filter(
    signal_in: np.ndarray, delay_samples: int, feedback: float
) -> np.ndarray:
    """single allpass filter."""
    output = np.zeros_like(signal_in)
    buf = np.zeros(delay_samples, dtype=np.float32)

    for i in range(len(signal_in)):
        buf_idx = i % delay_samples
        buf_out = buf[buf_idx]
        output[i] = -signal_in[i] + buf_out
        buf[buf_idx] = signal_in[i] + buf_out * feedback

    return output


def reverb(
    buffer: np.ndarray,
    wet: float = 0.3,
    decay: str = "medium",
    sample_rate: int = 48000,
) -> np.ndarray:
    """apply a schroeder-style reverb.

    decay: 'short', 'medium', 'long'
    wet: 0.0 (dry) to 1.0 (fully wet)
    """
    wet = max(0.0, min(1.0, wet))

    # feedback based on decay
    feedback_map = {"short": 0.6, "medium": 0.75, "long": 0.85}
    feedback = feedback_map.get(decay, 0.75)

    # process mono or per-channel
    if buffer.ndim == 1:
        processed = _apply_reverb_mono(buffer, feedback, sample_rate)
        return (buffer * (1 - wet) + processed * wet).astype(np.float32)

    result = np.zeros_like(buffer)
    for ch in range(buffer.shape[1]):
        processed = _apply_reverb_mono(buffer[:, ch], feedback, sample_rate)
        result[:, ch] = (buffer[:, ch] * (1 - wet) + processed * wet).astype(np.float32)

    return result


def _apply_reverb_mono(
    mono: np.ndarray, feedback: float, sample_rate: int
) -> np.ndarray:
    """apply reverb to a mono signal using parallel combs + series allpasses."""
    # 4 parallel comb filters with prime-ish delays
    comb_delays = [
        int(0.0297 * sample_rate),  # ~1427
        int(0.0371 * sample_rate),  # ~1781
        int(0.0411 * sample_rate),  # ~1973
        int(0.0437 * sample_rate),  # ~2098
    ]

    combs = np.zeros_like(mono)
    for delay in comb_delays:
        combs += _comb_filter(mono, delay, feedback)
    combs /= len(comb_delays)

    # 2 series allpass filters
    allpass_delays = [
        int(0.005 * sample_rate),  # ~240
        int(0.0017 * sample_rate),  # ~82
    ]

    result = combs
    for delay in allpass_delays:
        result = _allpass_filter(result, max(1, delay), 0.5)

    return result


def spatial_far(
    buffer: np.ndarray,
    sample_rate: int = 48000,
) -> np.ndarray:
    """make a sound feel far away: lower volume + reverb + slight lowpass."""
    from oram.dsp.filter import lowpass

    # lower volume
    quiet = buffer * 0.4
    # add reverb
    reverbed = reverb(quiet, wet=0.6, decay="long", sample_rate=sample_rate)
    # slight lowpass (distance absorbs highs)
    return lowpass(reverbed, cutoff_hz=3000, sample_rate=sample_rate)
