"""oram.dsp.flanger — swept short-delay comb (jet whoosh).

feedforward with a cascaded second pass standing in for feedback
resonance: keeps the render fully vectorized while still carving the
moving comb notches deep.
"""

from __future__ import annotations

import numpy as np

from oram.dsp.safety import sanitize_signal
from oram.dsp.util import equal_power_mix, modulated_delay_read, per_channel


def flanger(
    buffer: np.ndarray,
    rate_hz: float = 0.3,
    depth: float = 0.7,
    wet: float = 0.5,
    sample_rate: int = 44100,
) -> np.ndarray:
    """sweep a 0.3–6 ms delay against the dry signal.

    rate_hz: sweep speed
    depth: 0–1, how far the comb travels
    """
    dry = sanitize_signal(buffer)
    n = dry.shape[0]
    if n == 0 or wet <= 0.0:
        return dry.copy()

    rate_hz = float(np.clip(rate_hz, 0.05, 5.0))
    depth = float(np.clip(depth, 0.0, 1.0))

    min_ms, max_ms = 0.3, 0.3 + 5.7 * depth
    t = np.arange(n, dtype=np.float64) / sample_rate

    def _one_channel(x: np.ndarray, ch: int) -> np.ndarray:
        phase = ch * np.pi * 0.5
        lfo = 0.5 + 0.5 * np.sin(2.0 * np.pi * rate_hz * t + phase)
        delays = (min_ms + (max_ms - min_ms) * lfo) * sample_rate / 1000.0
        pass1 = x + modulated_delay_read(x, delays)
        # second cascaded pass deepens the notches (resonance stand-in)
        pass2 = pass1 + 0.6 * modulated_delay_read(pass1, delays)
        return (pass2 * 0.4).astype(np.float32)

    wet_sig = per_channel(_one_channel, dry)
    return sanitize_signal(equal_power_mix(dry, wet_sig, wet))
