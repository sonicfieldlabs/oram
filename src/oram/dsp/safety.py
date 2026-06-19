"""Safety helpers for offline DSP output.

These helpers run outside the realtime audio callback.  They keep generated
or transformed buffers finite, stereo-compatible, and click-safer before a
worker swaps them into a live layer.
"""

from __future__ import annotations

import numpy as np


def sanitize_signal(buffer: np.ndarray, peak: float = 0.98) -> np.ndarray:
    """Return a finite float32 copy, preserving the input dimensionality."""
    arr = np.array(buffer, dtype=np.float32, copy=True, order="C")
    if arr.size == 0:
        return arr

    np.nan_to_num(arr, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    max_peak = float(np.max(np.abs(arr)))
    if peak > 0.0 and max_peak > peak:
        arr *= peak / max_peak
    return arr


def coerce_audio_buffer(buffer: np.ndarray) -> np.ndarray:
    """Normalize a buffer to finite contiguous stereo float32 audio."""
    arr = sanitize_signal(buffer)

    if arr.ndim == 1:
        arr = np.column_stack([arr, arr])
    elif arr.ndim == 2:
        if arr.shape[1] == 0:
            raise ValueError("audio buffer must have at least one channel")
        if arr.shape[1] == 1:
            arr = np.column_stack([arr[:, 0], arr[:, 0]])
        elif arr.shape[1] > 2:
            arr = arr[:, :2]
    else:
        raise ValueError("audio buffer must be 1D or 2D")

    return sanitize_signal(arr)


def _smoothstep(length: int) -> np.ndarray:
    x = np.linspace(0.0, 1.0, length, dtype=np.float32)
    return x * x * (3.0 - 2.0 * x)


def smooth_edges(
    buffer: np.ndarray,
    sample_rate: int = 44100,
    fade_ms: float = 4.0,
) -> np.ndarray:
    """Apply a tiny equal-slope edge fade to avoid boundary clicks."""
    arr = coerce_audio_buffer(buffer)
    if arr.shape[0] < 4 or sample_rate <= 0 or fade_ms <= 0.0:
        return arr

    fade_samples = int(sample_rate * fade_ms / 1000.0)
    fade_samples = min(max(2, fade_samples), arr.shape[0] // 2)
    if fade_samples < 2:
        return arr

    fade = _smoothstep(fade_samples)
    arr[:fade_samples] *= fade[:, np.newaxis]
    arr[-fade_samples:] *= fade[::-1, np.newaxis]
    return arr


def crossfade_from_reference(
    processed: np.ndarray,
    reference: np.ndarray,
    processed_start: int,
    reference_start: int,
    sample_rate: int = 44100,
    fade_ms: float = 8.0,
) -> np.ndarray:
    """Blend the first live samples after a swap from old audio to new audio.

    The mixer has no transition state, so the worker pre-bakes a short ramp at
    the current playhead.  The first sample remains the old layer audio and the
    following few milliseconds become the processed signal.
    """
    out = coerce_audio_buffer(processed)
    ref = coerce_audio_buffer(reference)
    if out.shape[0] == 0 or ref.shape[0] == 0 or sample_rate <= 0 or fade_ms <= 0.0:
        return out

    fade_samples = int(sample_rate * fade_ms / 1000.0)
    fade_samples = min(max(2, fade_samples), out.shape[0], ref.shape[0])
    if fade_samples < 2:
        return out

    out_indices = (np.arange(fade_samples) + int(processed_start)) % out.shape[0]
    ref_indices = (np.arange(fade_samples) + int(reference_start)) % ref.shape[0]
    fade = _smoothstep(fade_samples)[:, np.newaxis]
    out[out_indices] = ref[ref_indices] * (1.0 - fade) + out[out_indices] * fade
    return sanitize_signal(out)


def prepare_dsp_output(
    buffer: np.ndarray,
    sample_rate: int = 44100,
    fade_edges: bool = True,
) -> np.ndarray:
    """Final validation pass for buffers returned by offline effects."""
    arr = smooth_edges(buffer, sample_rate=sample_rate) if fade_edges else coerce_audio_buffer(buffer)
    return sanitize_signal(arr)
