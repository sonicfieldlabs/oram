"""oram.dsp.delay — damped feedback delay with loop-aware tail.

poetic mappings:
- 'echo it'            -> defaults
- 'ping pong delay'    -> pingpong=True
- 'long delay'         -> time_ms up, feedback up
"""

from __future__ import annotations

import numpy as np

from oram.dsp.safety import sanitize_signal
from oram.dsp.util import equal_power_mix, feedback_comb, wrap_tail_into_loop


def delay(
    buffer: np.ndarray,
    time_ms: float = 350.0,
    feedback: float = 0.45,
    wet: float = 0.35,
    damp: float = 0.35,
    pingpong: bool = False,
    sample_rate: int = 44100,
    tail: str = "wrap",
) -> np.ndarray:
    """feedback echo.  each repeat passes a one-pole damping filter so the
    tail darkens naturally instead of ringing.

    time_ms: delay time (20–2000 ms)
    feedback: repeat amount (0–0.95)
    wet: equal-power wet mix
    pingpong: alternate repeats left/right
    tail: 'wrap' folds repeats past the loop end back into the loop start
    """
    dry = sanitize_signal(buffer)
    if dry.shape[0] == 0 or wet <= 0.0:
        return dry.copy()

    time_ms = float(np.clip(time_ms, 20.0, 2000.0))
    feedback = float(np.clip(feedback, 0.0, 0.95))
    delay_samples = max(1, int(time_ms * sample_rate / 1000.0))

    # render enough tail for the echoes to fall below -60 dB
    if feedback > 0.0:
        repeats_to_silence = int(np.ceil(-3.0 / np.log10(max(feedback, 0.05))))
    else:
        repeats_to_silence = 1
    tail_samples = min(delay_samples * (repeats_to_silence + 1), sample_rate * 8)

    padded_len = dry.shape[0] + tail_samples
    if dry.ndim == 1:
        x = np.zeros(padded_len, dtype=np.float32)
        x[: dry.shape[0]] = dry
        echoed = feedback_comb(x, delay_samples, feedback, damp) - x  # wet only
        wet_body, wet_tail = echoed[: dry.shape[0]], echoed[dry.shape[0]:]
    else:
        x = np.zeros((padded_len, dry.shape[1]), dtype=np.float32)
        x[: dry.shape[0]] = dry
        echoed = np.zeros_like(x)
        if pingpong and dry.shape[1] == 2:
            # feed the summed input into a mono loop, distribute odd/even
            # repeats to alternating channels via two offset taps
            mono = (x[:, 0] + x[:, 1]) * 0.5
            loop = feedback_comb(mono, delay_samples, feedback, damp) - mono
            # odd repeats (first echo) left, even repeats right: the right tap
            # is the loop delayed one more period, scaled by feedback
            right = np.zeros_like(loop)
            right[delay_samples:] = loop[:-delay_samples] * feedback
            echoed[:, 0] = loop - right  # remove even repeats from left
            echoed[:, 1] = right
        else:
            for ch in range(dry.shape[1]):
                col = np.ascontiguousarray(x[:, ch])
                echoed[:, ch] = feedback_comb(col, delay_samples, feedback, damp) - col
        wet_body, wet_tail = echoed[: dry.shape[0]], echoed[dry.shape[0]:]

    if tail == "extend":
        pad = ((0, wet_tail.shape[0]),) + ((0, 0),) * (dry.ndim - 1)
        dry_ext = np.pad(dry, pad)
        wet_sig = np.concatenate([wet_body, wet_tail], axis=0)
        return sanitize_signal(equal_power_mix(dry_ext, wet_sig, wet))
    if tail == "wrap":
        wet_body = wrap_tail_into_loop(wet_body, wet_tail)

    return sanitize_signal(equal_power_mix(dry, wet_body, wet))
