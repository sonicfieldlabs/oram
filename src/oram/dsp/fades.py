"""oram.dsp.fades — fade in/out and trim operations."""

from __future__ import annotations

import numpy as np


def fade_in(buffer: np.ndarray, duration_seconds: float = 1.0, sample_rate: int = 48000) -> np.ndarray:
    """apply a linear fade-in."""
    fade_samples = min(int(duration_seconds * sample_rate), buffer.shape[0])
    if fade_samples <= 0:
        return buffer.copy()
    result = buffer.copy()

    fade = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    if result.ndim > 1:
        fade = fade[:, np.newaxis]

    result[:fade_samples] *= fade
    return result


def fade_out(buffer: np.ndarray, duration_seconds: float = 1.0, sample_rate: int = 48000) -> np.ndarray:
    """apply a linear fade-out."""
    fade_samples = min(int(duration_seconds * sample_rate), buffer.shape[0])
    if fade_samples <= 0:
        return buffer.copy()
    result = buffer.copy()

    fade = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    if result.ndim > 1:
        fade = fade[:, np.newaxis]

    result[-fade_samples:] *= fade
    return result


def trim_start(buffer: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    """trim silence from the beginning of a buffer."""
    if buffer.ndim > 1:
        mono = np.max(np.abs(buffer), axis=1)
    else:
        mono = np.abs(buffer)

    # find first sample above threshold
    above = np.where(mono > threshold)[0]
    if len(above) == 0:
        return buffer.copy()

    start = max(0, above[0] - 100)  # keep a small margin
    return buffer[start:].copy()


def trim_end(buffer: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    """trim silence from the end of a buffer."""
    if buffer.ndim > 1:
        mono = np.max(np.abs(buffer), axis=1)
    else:
        mono = np.abs(buffer)

    above = np.where(mono > threshold)[0]
    if len(above) == 0:
        return buffer.copy()

    end = min(len(buffer), above[-1] + 100)  # keep margin
    return buffer[:end].copy()
