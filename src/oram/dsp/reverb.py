"""oram.dsp.reverb — vectorized Freeverb-style reverb with loop-aware tails.

spatial command mappings:
- 'far away' -> lower volume, more reverb, slight lowpass
- 'small room' -> short decay
- 'wash it in reverb' -> higher wet mix

quality notes vs the old 4-comb Schroeder:
- 8 damped feedback combs + 4 series allpasses per channel (Freeverb topology)
- damping inside the comb loop kills the metallic ring
- right channel runs detuned delays (+23 samples) for real stereo width
- predelay keeps the dry transient in front of the wash
- the tail is rendered past the buffer end and folded back into the loop
  start, so a loop keeps its length but the wash survives the seam
"""

from __future__ import annotations

import numpy as np

from oram.dsp.safety import sanitize_signal
from oram.dsp.util import (
    allpass_diffuser,
    equal_power_mix,
    feedback_comb,
    wrap_tail_into_loop,
)

# Freeverb delay sets, tuned at 44100 Hz and scaled to the session rate.
_COMB_DELAYS_44K = (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)
_ALLPASS_DELAYS_44K = (556, 441, 341, 225)
_STEREO_SPREAD = 23

_DECAY_PRESETS = {
    # feedback, damping, tail seconds
    "short": (0.72, 0.45, 0.8),
    "medium": (0.80, 0.32, 1.6),
    "long": (0.86, 0.22, 3.2),
}


def _reverb_channel(
    x: np.ndarray,
    sample_rate: int,
    feedback: float,
    damp: float,
    spread: int,
) -> np.ndarray:
    scale = sample_rate / 44100.0
    wet = np.zeros_like(x)
    for base in _COMB_DELAYS_44K:
        delay = max(1, int(round((base + spread) * scale)))
        wet += feedback_comb(x, delay, feedback, damp)
    wet /= len(_COMB_DELAYS_44K)
    for base in _ALLPASS_DELAYS_44K:
        delay = max(1, int(round((base + spread) * scale)))
        wet = allpass_diffuser(wet, delay, 0.5)
    return wet


def render_reverb_tail(
    buffer: np.ndarray,
    wet: float = 0.3,
    decay: str = "medium",
    sample_rate: int = 44100,
    width: float = 1.0,
    predelay_ms: float = 12.0,
) -> tuple[np.ndarray, np.ndarray]:
    """render the wet signal plus its tail beyond the buffer end.

    returns (wet_body, wet_tail) where body has the input length.
    """
    dry = sanitize_signal(buffer)
    feedback, damp, tail_seconds = _DECAY_PRESETS.get(decay, _DECAY_PRESETS["medium"])
    tail_samples = int(tail_seconds * sample_rate)
    predelay = max(0, int(predelay_ms * sample_rate / 1000.0))

    mono_input = dry if dry.ndim == 1 else dry
    padded_len = dry.shape[0] + tail_samples
    if dry.ndim == 1:
        x = np.zeros(padded_len, dtype=np.float32)
        x[predelay:predelay + dry.shape[0]] = dry[: max(0, padded_len - predelay)]
        wet_sig = _reverb_channel(x, sample_rate, feedback, damp, 0)
        return wet_sig[: dry.shape[0]], wet_sig[dry.shape[0]:]

    channels = dry.shape[1]
    x = np.zeros((padded_len, channels), dtype=np.float32)
    body_len = min(dry.shape[0], max(0, padded_len - predelay))
    x[predelay:predelay + body_len] = dry[:body_len]
    wet_sig = np.zeros_like(x)
    for ch in range(channels):
        spread = _STEREO_SPREAD if ch % 2 == 1 else 0
        wet_sig[:, ch] = _reverb_channel(
            np.ascontiguousarray(x[:, ch]), sample_rate, feedback, damp, spread
        )

    if channels == 2 and width < 1.0:
        mid = (wet_sig[:, 0] + wet_sig[:, 1]) * 0.5
        side = (wet_sig[:, 0] - wet_sig[:, 1]) * 0.5 * float(max(0.0, width))
        wet_sig[:, 0] = mid + side
        wet_sig[:, 1] = mid - side

    del mono_input
    return wet_sig[: dry.shape[0]], wet_sig[dry.shape[0]:]


def reverb(
    buffer: np.ndarray,
    wet: float = 0.3,
    decay: str = "medium",
    sample_rate: int = 44100,
    width: float = 1.0,
    predelay_ms: float = 12.0,
    tail: str = "wrap",
) -> np.ndarray:
    """apply a Freeverb-style reverb.

    decay: 'short', 'medium', 'long'
    wet: 0.0 (dry) to 1.0 (fully wet), equal-power mixed
    width: stereo width of the wash (0 mono .. 1 full)
    tail: 'wrap' folds the tail into the loop start (loop keeps its length),
          'extend' appends it, 'cut' discards it
    """
    wet = max(0.0, min(1.0, wet))
    dry = sanitize_signal(buffer)
    if wet <= 0.0 or dry.shape[0] == 0:
        return dry.copy()

    wet_body, wet_tail = render_reverb_tail(
        dry,
        wet=wet,
        decay=decay,
        sample_rate=sample_rate,
        width=width,
        predelay_ms=predelay_ms,
    )

    if tail == "extend":
        pad = ((0, wet_tail.shape[0]),) + ((0, 0),) * (dry.ndim - 1)
        dry_ext = np.pad(dry, pad)
        wet_sig = np.concatenate([wet_body, wet_tail], axis=0)
        return sanitize_signal(equal_power_mix(dry_ext, wet_sig, wet))
    if tail == "wrap":
        wet_body = wrap_tail_into_loop(wet_body, wet_tail)

    return sanitize_signal(equal_power_mix(dry, wet_body, wet))


def spatial_far(
    buffer: np.ndarray,
    sample_rate: int = 44100,
) -> np.ndarray:
    """make a sound feel far away: lower volume + reverb + slight lowpass."""
    from oram.dsp.filter import lowpass

    # lower volume
    quiet = buffer * 0.4
    # add reverb
    reverbed = reverb(quiet, wet=0.6, decay="long", sample_rate=sample_rate)
    # slight lowpass (distance absorbs highs)
    return lowpass(reverbed, cutoff_hz=3000, sample_rate=sample_rate)
