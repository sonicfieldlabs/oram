"""oram.dsp.distortion — oversampled waveshaping saturation.

characters:
- 'soft' -> symmetric tanh, tube-ish compression
- 'warm' -> asymmetric bias adds even harmonics
- 'fuzz' -> hard-driven fold-back edge

the shaper runs at 4x rate to keep harmonics from aliasing back down,
and the output is RMS-matched to the input so drive changes color, not
loudness.
"""

from __future__ import annotations

import numpy as np

from oram.dsp.safety import sanitize_signal
from oram.dsp.util import equal_power_mix, match_rms, oversampled

_CHARACTERS = ("soft", "warm", "fuzz")


def distortion(
    buffer: np.ndarray,
    drive: float = 4.0,
    character: str = "soft",
    tone_hz: float | None = None,
    wet: float = 1.0,
    sample_rate: int = 44100,
) -> np.ndarray:
    """drive the signal into a nonlinear shaper.

    drive: 1–20
    character: soft / warm / fuzz
    tone_hz: optional post-shaper lowpass
    """
    dry = sanitize_signal(buffer)
    if dry.shape[0] == 0 or wet <= 0.0:
        return dry.copy()

    drive = float(np.clip(drive, 1.0, 20.0))
    if character not in _CHARACTERS:
        character = "soft"

    def _shape(x: np.ndarray) -> np.ndarray:
        if character == "warm":
            biased = x * drive + 0.12 * np.square(x * drive) * np.sign(x)
            shaped = np.tanh(biased)
        elif character == "fuzz":
            hot = x * drive * 1.6
            shaped = np.tanh(hot) + 0.15 * np.sin(2.5 * np.clip(hot, -np.pi, np.pi))
        else:
            shaped = np.tanh(x * drive)
        return shaped.astype(np.float32)

    shaped = oversampled(_shape, dry, factor=4)

    if tone_hz is not None and tone_hz > 0:
        from oram.dsp.filter import lowpass

        shaped = lowpass(shaped, cutoff_hz=float(tone_hz), sample_rate=sample_rate)

    shaped = match_rms(shaped, dry)
    return sanitize_signal(equal_power_mix(dry, shaped, wet))
