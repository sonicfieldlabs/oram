"""oram.dsp.chorus — multi-voice modulated delay.

three detuned voices per channel, LFO phases spread across channels for
width.  feedforward only, so the whole render is vectorized interpolated
reads — no per-sample python.
"""

from __future__ import annotations

import numpy as np

from oram.dsp.safety import sanitize_signal
from oram.dsp.util import equal_power_mix, modulated_delay_read, per_channel


def chorus(
    buffer: np.ndarray,
    rate_hz: float = 0.6,
    depth_ms: float = 6.0,
    voices: int = 3,
    wet: float = 0.5,
    sample_rate: int = 44100,
) -> np.ndarray:
    """classic chorus: short modulated delays around ~20 ms.

    rate_hz: LFO speed (0.05–8 Hz)
    depth_ms: modulation depth
    voices: number of detuned voices (1–4)
    """
    dry = sanitize_signal(buffer)
    n = dry.shape[0]
    if n == 0 or wet <= 0.0:
        return dry.copy()

    rate_hz = float(np.clip(rate_hz, 0.05, 8.0))
    depth_ms = float(np.clip(depth_ms, 0.5, 20.0))
    voices = int(np.clip(voices, 1, 4))

    base_ms = 18.0
    t = np.arange(n, dtype=np.float64) / sample_rate
    depth_samples = depth_ms * sample_rate / 1000.0
    base_samples = base_ms * sample_rate / 1000.0

    def _one_channel(x: np.ndarray, ch: int) -> np.ndarray:
        out = np.zeros_like(x, dtype=np.float32)
        for v in range(voices):
            phase = (v / voices + ch * 0.25) * 2.0 * np.pi
            voice_rate = rate_hz * (1.0 + 0.13 * v)
            lfo = np.sin(2.0 * np.pi * voice_rate * t + phase)
            delays = base_samples * (1.0 + 0.2 * v) + depth_samples * (0.5 + 0.5 * lfo)
            out += modulated_delay_read(x, delays)
        return out / np.float32(voices)

    wet_sig = per_channel(_one_channel, dry)
    return sanitize_signal(equal_power_mix(dry, wet_sig, wet))
