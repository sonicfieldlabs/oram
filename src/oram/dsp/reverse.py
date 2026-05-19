"""oram.dsp.reverse — buffer reversal."""

from __future__ import annotations

import numpy as np


def reverse(buffer: np.ndarray) -> np.ndarray:
    """reverse audio buffer along the time axis.

    preserves channel layout. returns a new array.
    """
    return np.flip(buffer, axis=0).copy()
