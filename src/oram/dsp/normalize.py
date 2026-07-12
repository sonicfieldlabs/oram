"""oram.dsp.normalize — loudness normalization."""

from __future__ import annotations

import numpy as np

from oram.dsp.safety import sanitize_signal


def normalize(
    buffer: np.ndarray,
    target_db: float = -1.0,
    mode: str = "peak",
    sample_rate: int = 44100,
) -> np.ndarray:
    """normalize level.

    mode 'peak': scale the highest peak to target_db (dBFS)
    mode 'rms': scale average energy to target_db, peak-guarded at -0.5 dB
    """
    audio = np.array(buffer, dtype=np.float32, copy=True)
    if audio.size == 0:
        return audio

    target_db = float(np.clip(target_db, -24.0, 0.0))
    target = 10.0 ** (target_db / 20.0)

    if mode == "rms":
        rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if rms <= 1e-9:
            return audio
        audio *= np.float32(target / rms)
        peak = float(np.max(np.abs(audio)))
        ceiling = 10.0 ** (-0.5 / 20.0)
        if peak > ceiling:
            audio *= np.float32(ceiling / peak)
        return sanitize_signal(audio, peak=1.0)

    peak = float(np.max(np.abs(audio)))
    if peak <= 1e-9:
        return audio
    audio *= np.float32(target / peak)
    return sanitize_signal(audio, peak=1.0)
