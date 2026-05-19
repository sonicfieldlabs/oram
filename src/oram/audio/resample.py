"""oram.audio.resample — sample rate conversion and format normalization.

ensures all generated/imported audio matches the session sample rate
and is in stereo float32 format.
"""

from __future__ import annotations

import numpy as np


def ensure_stereo_float32(
    audio: np.ndarray,
    source_sr: int,
    target_sr: int,
) -> np.ndarray:
    """convert audio to stereo float32 at the target sample rate.

    1. ensure float32
    2. ensure stereo (mono -> dual-mono)
    3. resample if source_sr != target_sr
    4. conservative normalization
    """
    # 1. ensure float32
    audio = audio.astype(np.float32)

    if audio.size == 0:
        if audio.ndim == 1:
            return np.zeros((0, 2), dtype=np.float32)
        channels = audio.shape[1] if audio.ndim == 2 and audio.shape[1] > 0 else 2
        channels = 2 if channels != 2 else channels
        return np.zeros((0, channels), dtype=np.float32)

    # 2. ensure stereo
    if audio.ndim == 1:
        audio = np.column_stack([audio, audio])
    elif audio.ndim == 2 and audio.shape[1] == 1:
        audio = np.column_stack([audio[:, 0], audio[:, 0]])
    elif audio.ndim == 2 and audio.shape[1] > 2:
        audio = audio[:, :2]
    elif audio.ndim != 2:
        raise ValueError("audio must be 1D mono or 2D channel-major samples")

    # 3. resample if needed
    if source_sr != target_sr and source_sr > 0 and target_sr > 0:
        audio = _resample(audio, source_sr, target_sr)

    # 4. normalize if too hot (prevent dominating the mix)
    peak = np.max(np.abs(audio))
    if peak > 0.95:
        audio = audio * (0.9 / peak)

    return audio


def _resample(audio: np.ndarray, source_sr: int, target_sr: int) -> np.ndarray:
    """resample stereo audio using scipy.signal.resample.

    preserves the duration of the audio.
    """
    from scipy.signal import resample

    ratio = target_sr / source_sr
    new_length = int(audio.shape[0] * ratio)
    if new_length <= 0:
        return np.zeros((0, audio.shape[1]), dtype=np.float32)

    resampled = resample(audio, new_length, axis=0).astype(np.float32)
    return resampled
