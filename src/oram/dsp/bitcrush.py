"""oram.dsp.bitcrush — bit-depth and sample-rate reduction.

quantize to `bits` and hold every `downsample`-th sample: the classic
grit.  both stages are pure vector math.
"""

from __future__ import annotations

import numpy as np

from oram.dsp.safety import sanitize_signal
from oram.dsp.util import equal_power_mix


def bitcrush(
    buffer: np.ndarray,
    bits: int = 8,
    downsample: int = 4,
    wet: float = 1.0,
    sample_rate: int = 44100,
) -> np.ndarray:
    """crush resolution.

    bits: target bit depth (2–16)
    downsample: sample-hold factor (1 = off, 4 = quarter rate)
    """
    dry = sanitize_signal(buffer)
    if dry.shape[0] == 0 or wet <= 0.0:
        return dry.copy()

    bits = int(np.clip(bits, 2, 16))
    downsample = int(np.clip(downsample, 1, 64))

    crushed = dry
    if downsample > 1:
        hold_indices = (np.arange(dry.shape[0]) // downsample) * downsample
        crushed = dry[hold_indices]

    levels = float(2 ** (bits - 1))
    crushed = np.round(crushed * levels) / levels

    return sanitize_signal(equal_power_mix(dry, crushed.astype(np.float32), wet))
