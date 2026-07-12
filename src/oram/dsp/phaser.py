"""oram.dsp.phaser — LFO-swept allpass ladder.

six first-order allpass stages with block-interpolated coefficients:
the LFO is sampled every 128 samples and each block runs through
scipy.lfilter with carried state, so the sweep stays smooth without a
per-sample python loop.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from oram.dsp.safety import sanitize_signal
from oram.dsp.util import equal_power_mix, per_channel

_BLOCK = 128


def phaser(
    buffer: np.ndarray,
    rate_hz: float = 0.4,
    depth: float = 0.8,
    stages: int = 6,
    wet: float = 0.5,
    sample_rate: int = 44100,
) -> np.ndarray:
    """sweep allpass notches between ~200 Hz and ~2.2 kHz.

    rate_hz: sweep speed
    depth: 0–1, sweep range
    stages: allpass stage count (2–8, even)
    """
    dry = sanitize_signal(buffer)
    n = dry.shape[0]
    if n == 0 or wet <= 0.0:
        return dry.copy()

    rate_hz = float(np.clip(rate_hz, 0.05, 4.0))
    depth = float(np.clip(depth, 0.0, 1.0))
    stages = int(np.clip(stages, 2, 8))

    low_hz, high_hz = 200.0, 200.0 + 2000.0 * depth
    n_blocks = int(np.ceil(n / _BLOCK))
    block_t = (np.arange(n_blocks) * _BLOCK + _BLOCK / 2) / sample_rate

    def _one_channel(x: np.ndarray, ch: int) -> np.ndarray:
        phase = ch * np.pi * 0.5
        lfo = 0.5 + 0.5 * np.sin(2.0 * np.pi * rate_hz * block_t + phase)
        centers = low_hz * (high_hz / low_hz) ** lfo  # exponential sweep
        tan_arg = np.clip(np.pi * centers / sample_rate, 1e-4, np.pi / 2 - 1e-3)
        coeffs = (np.tan(tan_arg) - 1.0) / (np.tan(tan_arg) + 1.0)

        y = x.astype(np.float64)
        zis = [np.zeros(1) for _ in range(stages)]
        out = np.empty_like(y)
        for b in range(n_blocks):
            start = b * _BLOCK
            stop = min(start + _BLOCK, n)
            seg = y[start:stop]
            a1 = float(coeffs[b])
            b_coef = np.array([a1, 1.0])
            a_coef = np.array([1.0, a1])
            for s in range(stages):
                seg, zis[s] = lfilter(b_coef, a_coef, seg, zi=zis[s])
            out[start:stop] = seg
        return out.astype(np.float32)

    swept = per_channel(_one_channel, dry)
    wet_sig = ((dry + swept) * 0.5).astype(np.float32)
    return sanitize_signal(equal_power_mix(dry, wet_sig, wet))
