"""oram.dsp.granular — simple offline granular processing.

poetic mappings:
- 'granulate softly' -> density 0.25-0.4, jitter 0.1-0.25, low wet
- 'turn into dust' -> higher density/jitter, lower dry
- 'stretch until it breathes' -> slower speed + granular smear + reverb
"""

from __future__ import annotations

import numpy as np


def granular(
    buffer: np.ndarray,
    density: float = 0.3,
    grain_size_ms: float = 120.0,
    jitter: float = 0.15,
    sample_rate: int = 48000,
    wet: float = 0.6,
) -> np.ndarray:
    """apply offline granular processing.

    1. split buffer into grains
    2. randomize grain start positions by jitter amount
    3. apply window (hann) to each grain
    4. overlap-add into output

    density: 0.0-1.0 controls how many grains are emitted
    grain_size_ms: grain duration in milliseconds
    jitter: 0.0-1.0 randomization of grain start positions
    wet: dry/wet mix ratio
    """
    grain_samples = max(64, int(grain_size_ms * sample_rate / 1000))
    length = buffer.shape[0]

    if length < grain_samples:
        return buffer.copy()

    # determine mono or stereo
    is_stereo = buffer.ndim > 1

    # output buffer
    output = np.zeros_like(buffer, dtype=np.float32)

    # hop between grains (density controls overlap)
    hop = max(1, int(grain_samples * (1.0 - density * 0.8)))

    # hann window
    window = np.hanning(grain_samples).astype(np.float32)
    if is_stereo:
        window_2d = window[:, np.newaxis]

    # generate grains
    position = 0
    while position < length:
        # randomize start position
        max_jitter = int(length * jitter)
        grain_start = position + np.random.randint(-max_jitter, max_jitter + 1)
        grain_start = max(0, min(grain_start, length - grain_samples))

        # extract grain
        grain_end = grain_start + grain_samples
        if grain_end > length:
            break

        grain = buffer[grain_start:grain_end].copy()

        # apply window
        if is_stereo:
            grain *= window_2d
        else:
            grain *= window

        # place in output at current position
        out_end = min(position + grain_samples, length)
        out_len = out_end - position
        output[position:out_end] += grain[:out_len]

        position += hop

    # normalize output to prevent clipping
    peak = np.max(np.abs(output))
    if peak > 0.0:
        output = output / peak * np.max(np.abs(buffer))

    # wet/dry mix
    return (buffer * (1 - wet) + output * wet).astype(np.float32)


def stretch_breathe(
    buffer: np.ndarray,
    sample_rate: int = 48000,
) -> np.ndarray:
    """stretch until it breathes: slower speed + granular smear + reverb.

    combines speed reduction, granular processing, and reverb
    for a breathing, expansive texture.
    """
    from oram.dsp.reverb import reverb
    from oram.dsp.speed import change_speed

    # slow down
    stretched = change_speed(buffer, ratio=0.6, sample_rate=sample_rate)

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
