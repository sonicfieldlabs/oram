"""oram.dsp.spatial — near / wide spatial placement.

the spatial family:
- far  -> reverb.spatial_far (distance: quieter, washed, darker)
- near -> presence lift + slight width narrowing (intimate, in front)
- wide -> Haas offset + mid/side widening, mono-compatible
"""

from __future__ import annotations

import numpy as np
from scipy.signal import sosfiltfilt

from oram.dsp.safety import coerce_audio_buffer, sanitize_signal


def _high_shelf_sos(gain_db: float, freq_hz: float, sample_rate: int) -> np.ndarray:
    """RBJ high-shelf biquad as a single SOS section."""
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * min(freq_hz, sample_rate * 0.45) / sample_rate
    cos_w0 = np.cos(w0)
    alpha = np.sin(w0) / 2.0 * np.sqrt((a + 1.0 / a) * (1.0 / 0.9 - 1.0) + 2.0)
    sqrt_a = 2.0 * np.sqrt(a) * alpha

    b0 = a * ((a + 1) + (a - 1) * cos_w0 + sqrt_a)
    b1 = -2 * a * ((a - 1) + (a + 1) * cos_w0)
    b2 = a * ((a + 1) + (a - 1) * cos_w0 - sqrt_a)
    a0 = (a + 1) - (a - 1) * cos_w0 + sqrt_a
    a1 = 2 * ((a - 1) - (a + 1) * cos_w0)
    a2 = (a + 1) - (a - 1) * cos_w0 - sqrt_a
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])


def spatial_near(buffer: np.ndarray, sample_rate: int = 44100) -> np.ndarray:
    """bring a sound close: presence shelf, gentle lift, slightly narrower."""
    audio = coerce_audio_buffer(buffer)
    if audio.shape[0] < 32:
        return audio

    sos = _high_shelf_sos(2.5, 3200.0, sample_rate)
    bright = np.empty_like(audio)
    for ch in range(audio.shape[1]):
        bright[:, ch] = sosfiltfilt(sos, audio[:, ch]).astype(np.float32)

    # narrow the image a touch — close sources have less side energy
    mid = (bright[:, 0] + bright[:, 1]) * 0.5
    side = (bright[:, 0] - bright[:, 1]) * 0.5 * 0.6
    out = np.column_stack([mid + side, mid - side]) * 1.12
    return sanitize_signal(out)


def spatial_wide(
    buffer: np.ndarray,
    sample_rate: int = 44100,
    width: float = 1.5,
    haas_ms: float = 12.0,
) -> np.ndarray:
    """widen the stereo image: Haas offset + mid/side widening.

    stays mono-compatible: the mid channel is untouched, only the side
    level rises, and the Haas voice is mixed 20% under the direct signal.
    """
    audio = coerce_audio_buffer(buffer)
    n = audio.shape[0]
    if n < 32:
        return audio

    haas = max(1, min(int(haas_ms * sample_rate / 1000.0), n - 1))
    delayed_left = np.zeros(n, dtype=np.float32)
    delayed_left[haas:] = audio[:-haas, 1]
    wide = audio.copy()
    wide[:, 0] += delayed_left * 0.2

    mid = (wide[:, 0] + wide[:, 1]) * 0.5
    side = (wide[:, 0] - wide[:, 1]) * 0.5 * float(np.clip(width, 0.0, 2.0))
    out = np.column_stack([mid + side, mid - side])
    return sanitize_signal(out)
