"""oram.dsp.granular — offline granular processing.

poetic mappings:
- 'granulate softly' -> density 0.25-0.4, jitter 0.1-0.25, low wet
- 'turn into dust' -> higher density/jitter, lower dry
- 'stretch until it breathes' -> phase-vocoder stretch + granular smear + reverb

vs the old version: jitter now sprays grains inside a local window
(4 grains wide) instead of across the whole buffer, grain sizes vary
±30%, and heavy jitter flips some grains backwards — small-scale cloud
instead of random full-buffer shuffle.
"""

from __future__ import annotations

import numpy as np

from oram.dsp.safety import sanitize_signal
from oram.dsp.util import equal_power_mix


def granular(
    buffer: np.ndarray,
    density: float = 0.3,
    grain_size_ms: float = 120.0,
    jitter: float = 0.15,
    sample_rate: int = 44100,
    wet: float = 0.6,
    seed: int | None = None,
) -> np.ndarray:
    """apply offline granular processing.

    1. walk the buffer in hops (density controls overlap)
    2. spray each grain's read position inside a local window by jitter
    3. jitter grain sizes ±30%; occasionally reverse grains at high jitter
    4. hann-window and overlap-add, weight-compensated

    density: 0.0-1.0 controls grain overlap
    grain_size_ms: nominal grain duration in milliseconds
    jitter: 0.0-1.0 randomization of grain read positions
    wet: dry/wet mix ratio (equal-power)
    """
    audio = sanitize_signal(buffer)
    base_grain = max(64, int(grain_size_ms * sample_rate / 1000))
    length = audio.shape[0]

    if length < base_grain * 2:
        return audio.copy()

    is_stereo = audio.ndim > 1
    density = float(np.clip(density, 0.0, 1.0))
    jitter = float(np.clip(jitter, 0.0, 1.0))
    rng = np.random.default_rng(seed)

    output = np.zeros_like(audio, dtype=np.float32)
    weight = np.zeros(length, dtype=np.float32)

    hop = max(1, int(base_grain * (1.0 - density * 0.8)))
    spray = int(jitter * base_grain * 4)  # local window, not the whole buffer
    reverse_prob = 0.18 if jitter > 0.4 else 0.0

    position = 0
    while position < length:
        grain_samples = int(base_grain * rng.uniform(0.7, 1.3))
        grain_samples = max(64, min(grain_samples, length - 1))

        read_start = position + int(rng.integers(-spray, spray + 1)) if spray > 0 else position
        read_start = max(0, min(read_start, length - grain_samples))
        grain = audio[read_start:read_start + grain_samples].copy()

        if reverse_prob > 0.0 and rng.random() < reverse_prob:
            grain = grain[::-1]

        window = np.hanning(grain_samples).astype(np.float32)
        if is_stereo:
            grain *= window[:, np.newaxis]
        else:
            grain *= window

        out_end = min(position + grain_samples, length)
        out_len = out_end - position
        output[position:out_end] += grain[:out_len]
        weight[position:out_end] += window[:out_len]

        position += hop

    # compensate overlap-add gain so dense settings do not dip or jump.
    safe_weight = np.maximum(weight, 1e-4)
    if is_stereo:
        output /= safe_weight[:, np.newaxis]
    else:
        output /= safe_weight

    # keep the cloud at the input's level
    peak = float(np.max(np.abs(output)))
    input_peak = float(np.max(np.abs(audio)))
    if peak > 0.0 and input_peak > 0.0:
        output *= input_peak / peak

    return sanitize_signal(equal_power_mix(audio, output, wet))


def stretch_breathe(
    buffer: np.ndarray,
    sample_rate: int = 44100,
) -> np.ndarray:
    """stretch until it breathes: phase-vocoder stretch + granular smear + reverb.

    the stretch preserves pitch (the material slows without dropping),
    then the granular pass loosens the grain and the reverb opens the space.
    """
    from oram.dsp.reverb import reverb
    from oram.dsp.stretch import time_stretch

    # slow down without transposing
    stretched = time_stretch(buffer, ratio=1.7, sample_rate=sample_rate)

    # granular smear
    granulated = granular(
        stretched,
        density=0.4,
        grain_size_ms=200,
        jitter=0.3,
        sample_rate=sample_rate,
        wet=0.5,
    )

    # add reverb
    return reverb(granulated, wet=0.4, decay="long", sample_rate=sample_rate)
