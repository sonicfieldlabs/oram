"""oram.dsp.reverb — simple schroeder-style reverb.

spatial command mappings:
- 'far away' -> lower volume, more reverb, slight lowpass
- 'small room' -> short decay
- 'wash it in reverb' -> higher wet mix
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from oram.dsp.safety import sanitize_signal


def _comb_filter(
    signal_in: np.ndarray, delay_samples: int, feedback: float
) -> np.ndarray:
    """single feedback comb filter."""
    delay_samples = max(1, int(delay_samples))
    denominator = np.zeros(delay_samples + 1, dtype=np.float32)
    denominator[0] = 1.0
    denominator[-1] = -float(feedback)
    return lfilter([1.0], denominator, signal_in).astype(np.float32)


def _allpass_filter(
    signal_in: np.ndarray, delay_samples: int, feedback: float
) -> np.ndarray:
    """single Schroeder allpass filter."""
    delay_samples = max(1, int(delay_samples))
    numerator = np.zeros(delay_samples + 1, dtype=np.float32)
    denominator = np.zeros(delay_samples + 1, dtype=np.float32)
    numerator[0] = -float(feedback)
    numerator[-1] = 1.0
    denominator[0] = 1.0
    denominator[-1] = -float(feedback)
    return lfilter(numerator, denominator, signal_in).astype(np.float32)


def reverb(
    buffer: np.ndarray,
    wet: float = 0.3,
    decay: str = "medium",
    sample_rate: int = 44100,
) -> np.ndarray:
    """apply a schroeder-style reverb.

    decay: 'short', 'medium', 'long'
    wet: 0.0 (dry) to 1.0 (fully wet)
    """
    wet = max(0.0, min(1.0, wet))
    dry = sanitize_signal(buffer)
    if wet <= 0.0:
        return dry.copy()

    # feedback based on decay
    feedback_map = {"short": 0.6, "medium": 0.75, "long": 0.85}
    feedback = feedback_map.get(decay, 0.75)

    # process mono or per-channel
    if dry.ndim == 1:
        processed = _apply_reverb_mono(dry, feedback, sample_rate)
        return sanitize_signal(dry * (1 - wet) + processed * wet)

    result = np.zeros_like(dry)
    for ch in range(dry.shape[1]):
        processed = _apply_reverb_mono(dry[:, ch], feedback, sample_rate)
        result[:, ch] = (dry[:, ch] * (1 - wet) + processed * wet).astype(np.float32)

    return sanitize_signal(result)


def _apply_reverb_mono(
    mono: np.ndarray, feedback: float, sample_rate: int
) -> np.ndarray:
    """apply reverb to a mono signal using parallel combs + series allpasses."""
    # 4 parallel comb filters with prime-ish delays
    comb_delays = [
        int(0.0297 * sample_rate),
        int(0.0371 * sample_rate),
        int(0.0411 * sample_rate),
        int(0.0437 * sample_rate),
    ]

    combs = np.zeros_like(mono)
    for delay in comb_delays:
        combs += _comb_filter(mono, delay, feedback)
    combs /= len(comb_delays)

    # 2 series allpass filters
    allpass_delays = [
        int(0.005 * sample_rate),
        int(0.0017 * sample_rate),
    ]

    result = combs
    for delay in allpass_delays:
        result = _allpass_filter(result, max(1, delay), 0.5)

    return sanitize_signal(result)


def spatial_far(
    buffer: np.ndarray,
    sample_rate: int = 44100,
) -> np.ndarray:
    """make a sound feel far away: lower volume + reverb + slight lowpass."""
    from oram.dsp.filter import lowpass

    # lower volume
    quiet = buffer * 0.4
    # add reverb
    reverbed = reverb(quiet, wet=0.6, decay="long", sample_rate=sample_rate)
    # slight lowpass (distance absorbs highs)
    return lowpass(reverbed, cutoff_hz=3000, sample_rate=sample_rate)
