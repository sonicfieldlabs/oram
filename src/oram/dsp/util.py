"""oram.dsp.util — shared offline DSP building blocks.

Everything here runs outside the realtime callback.  These helpers keep the
effect modules small: equal-power mixing, loop-aware tails, vectorized
feedback lines (comb/allpass via delay-length chunking), fractional delay
reads, and oversampling for nonlinear stages.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter, resample_poly


def as_float32(buffer: np.ndarray) -> np.ndarray:
    """contiguous float32 view/copy without changing dimensionality."""
    return np.ascontiguousarray(buffer, dtype=np.float32)


def equal_power_mix(dry: np.ndarray, wet_signal: np.ndarray, wet: float) -> np.ndarray:
    """blend dry/wet with an equal-power law so 50% mixes don't lose energy."""
    wet = float(max(0.0, min(1.0, wet)))
    theta = wet * (np.pi / 2.0)
    dry_gain = np.float32(np.cos(theta))
    wet_gain = np.float32(np.sin(theta))
    n = min(dry.shape[0], wet_signal.shape[0])
    return (dry[:n] * dry_gain + wet_signal[:n] * wet_gain).astype(np.float32)


def wrap_tail_into_loop(body: np.ndarray, tail: np.ndarray) -> np.ndarray:
    """fold an effect tail back into the loop start.

    a looper cuts reverb/delay tails at the loop boundary; wrapping the tail
    keeps the loop length while letting the wash continue across the seam.
    """
    out = body.copy()
    loop_len = out.shape[0]
    if loop_len == 0 or tail.shape[0] == 0:
        return out
    position = 0
    remaining = tail
    while remaining.shape[0] > 0:
        n = min(loop_len - position, remaining.shape[0])
        out[position:position + n] += remaining[:n]
        remaining = remaining[n:]
        position = (position + n) % loop_len
    return out


def feedback_comb(
    x: np.ndarray,
    delay_samples: int,
    feedback: float,
    damp: float = 0.0,
) -> np.ndarray:
    """damped feedback comb, vectorized by processing in delay-length chunks.

    y[n] = x[n] + feedback * onepole(y[n - D])

    within one D-sized chunk every delayed sample comes from the previous
    chunk, so each chunk is a single vector operation; the one-pole damping
    state carries across chunks through lfilter's zi.
    """
    d = max(1, int(delay_samples))
    fb = float(np.clip(feedback, 0.0, 0.98))
    damp = float(np.clip(damp, 0.0, 0.99))
    n = x.shape[0]
    y = np.zeros(n + d, dtype=np.float32)  # d leading zeros = empty delay line
    b = np.array([1.0 - damp], dtype=np.float64)
    a = np.array([1.0, -damp], dtype=np.float64)
    zi = np.zeros(1, dtype=np.float64)
    for start in range(0, n, d):
        stop = min(start + d, n)
        delayed = y[start:stop]  # == y[(start-d)+d : ...] thanks to the offset
        if damp > 0.0:
            damped, zi = lfilter(b, a, delayed, zi=zi)
        else:
            damped = delayed
        y[start + d:stop + d] = x[start:stop] + fb * damped.astype(np.float32)
    return y[d:]


def allpass_diffuser(x: np.ndarray, delay_samples: int, gain: float = 0.5) -> np.ndarray:
    """Schroeder allpass y[n] = -g·x[n] + x[n-D] + g·y[n-D], chunk-vectorized."""
    d = max(1, int(delay_samples))
    g = float(np.clip(gain, -0.98, 0.98))
    n = x.shape[0]
    xp = np.zeros(n + d, dtype=np.float32)
    xp[d:] = x
    y = np.zeros(n + d, dtype=np.float32)
    for start in range(0, n, d):
        stop = min(start + d, n)
        y[start + d:stop + d] = -g * x[start:stop] + xp[start:stop] + g * y[start:stop]
    return y[d:]


def modulated_delay_read(
    x: np.ndarray,
    delay_samples: np.ndarray,
) -> np.ndarray:
    """read x at a per-sample fractional delay (feedforward, linear interp)."""
    n = x.shape[0]
    positions = np.arange(n, dtype=np.float64) - np.asarray(delay_samples, dtype=np.float64)
    np.clip(positions, 0.0, n - 1.0, out=positions)
    return np.interp(positions, np.arange(n, dtype=np.float64), x.astype(np.float64)).astype(np.float32)


def oversampled(fn, buffer: np.ndarray, factor: int = 4) -> np.ndarray:
    """run a nonlinear shaper at `factor`x rate to suppress aliasing."""
    up = resample_poly(buffer, factor, 1, axis=0)
    shaped = fn(up)
    down = resample_poly(shaped, 1, factor, axis=0)
    n = buffer.shape[0]
    if down.shape[0] >= n:
        return down[:n].astype(np.float32)
    pad = ((0, n - down.shape[0]),) + ((0, 0),) * (buffer.ndim - 1)
    return np.pad(down, pad).astype(np.float32)


def match_rms(processed: np.ndarray, reference: np.ndarray, limit: float = 0.98) -> np.ndarray:
    """scale processed audio so its RMS matches the reference, peak-guarded."""
    ref_rms = float(np.sqrt(np.mean(np.square(reference), dtype=np.float64)))
    out_rms = float(np.sqrt(np.mean(np.square(processed), dtype=np.float64)))
    if out_rms <= 1e-9 or ref_rms <= 1e-9:
        return processed.astype(np.float32)
    scaled = processed * np.float32(ref_rms / out_rms)
    peak = float(np.max(np.abs(scaled)))
    if peak > limit:
        scaled *= np.float32(limit / peak)
    return scaled.astype(np.float32)


def per_channel(fn, buffer: np.ndarray) -> np.ndarray:
    """apply a mono function per channel, preserving dimensionality."""
    if buffer.ndim == 1:
        return fn(buffer, 0).astype(np.float32)
    out = np.empty_like(buffer, dtype=np.float32)
    for ch in range(buffer.shape[1]):
        out[:, ch] = fn(np.ascontiguousarray(buffer[:, ch]), ch)
    return out
