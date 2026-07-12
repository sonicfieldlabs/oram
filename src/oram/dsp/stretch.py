"""oram.dsp.stretch — phase-vocoder time stretching with peak phase locking.

this is the "proper pitch shift preserving duration" the pitch module
promised: a vectorized STFT phase vocoder (Laroche–Dolson identity phase
locking) so a loop can stretch or transpose without losing its length.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-12


def _stft_frames(x: np.ndarray, n_fft: int, hop: int, window: np.ndarray) -> np.ndarray:
    """all windowed FFT frames at once: (frames, bins)."""
    n = x.shape[0]
    if n < n_fft:
        x = np.pad(x, (0, n_fft - n))
        n = x.shape[0]
    count = 1 + (n - n_fft) // hop
    strides = (x.strides[0] * hop, x.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(count, n_fft), strides=strides, writeable=False
    )
    return np.fft.rfft(frames * window, axis=1)


def time_stretch(
    buffer: np.ndarray,
    ratio: float,
    sample_rate: int = 44100,
    n_fft: int | None = None,
) -> np.ndarray:
    """stretch duration by `ratio` without changing pitch.

    ratio > 1.0 = longer, ratio < 1.0 = shorter.  phase-locked vocoder;
    falls back to a plain copy for degenerate inputs.
    """
    ratio = float(max(0.25, min(4.0, ratio)))
    if abs(ratio - 1.0) < 1e-4 or buffer.shape[0] == 0:
        return buffer.astype(np.float32, copy=True)

    if n_fft is None:
        n_fft = 1 << int(np.ceil(np.log2(max(256, sample_rate * 0.045))))
    hop = n_fft // 4

    mono_channels = [buffer] if buffer.ndim == 1 else [
        np.ascontiguousarray(buffer[:, ch]) for ch in range(buffer.shape[1])
    ]
    out_len = int(round(buffer.shape[0] * ratio))
    stretched = []
    for x in mono_channels:
        stretched.append(_stretch_mono(x.astype(np.float32), ratio, n_fft, hop, out_len))
    if buffer.ndim == 1:
        return stretched[0]
    return np.column_stack(stretched)


def _stretch_mono(x: np.ndarray, ratio: float, n_fft: int, hop: int, out_len: int) -> np.ndarray:
    window = np.hanning(n_fft).astype(np.float32)
    pad = n_fft  # analysis headroom at both ends
    xp = np.pad(x, (pad, pad + n_fft))
    spec = _stft_frames(xp, n_fft, hop, window)
    n_frames, n_bins = spec.shape
    if n_frames < 2:
        return np.resize(x, out_len).astype(np.float32)

    magnitudes = np.abs(spec)
    phases = np.angle(spec)
    omega = 2.0 * np.pi * np.arange(n_bins) * hop / n_fft  # expected advance/frame

    # output frame count for the stretched duration
    out_frames = max(2, int(np.ceil((out_len + 2 * pad) / hop)))
    time_steps = np.minimum(np.arange(out_frames) / ratio, n_frames - 1.0001)

    acc_phase = phases[0].copy()
    out_spec = np.empty((out_frames, n_bins), dtype=np.complex128)
    out_spec[0] = magnitudes[0] * np.exp(1j * acc_phase)

    frame_floor = np.floor(time_steps).astype(np.intp)
    frame_frac = (time_steps - frame_floor).astype(np.float64)

    for k in range(1, out_frames):
        f0 = frame_floor[k]
        f1 = min(f0 + 1, n_frames - 1)
        frac = frame_frac[k]
        mag = magnitudes[f0] * (1.0 - frac) + magnitudes[f1] * frac

        # instantaneous frequency between the two analysis frames
        dphi = phases[f1] - phases[f0] - omega
        dphi -= 2.0 * np.pi * np.round(dphi / (2.0 * np.pi))
        acc_phase = acc_phase + omega + dphi

        # identity phase locking: lock each bin to its nearest spectral peak
        locked = acc_phase
        if n_bins >= 5:
            is_peak = np.zeros(n_bins, dtype=bool)
            is_peak[2:-2] = (
                (mag[2:-2] >= mag[1:-3])
                & (mag[2:-2] >= mag[3:-1])
                & (mag[2:-2] >= mag[:-4])
                & (mag[2:-2] >= mag[4:])
            )
            peaks = np.flatnonzero(is_peak)
            if peaks.size > 0:
                borders = np.zeros(n_bins, dtype=np.intp)
                if peaks.size > 1:
                    mid = (peaks[:-1] + peaks[1:]) // 2 + 1
                    borders[mid] = 1
                owner = peaks[np.cumsum(borders)]
                # rotate every bin by its peak's accumulated-vs-analysis offset
                rotation = acc_phase[owner] - phases[f0][owner]
                locked = phases[f0] + rotation
                locked[peaks] = acc_phase[peaks]
                acc_phase = locked

        out_spec[k] = mag * np.exp(1j * locked)

    frames_time = np.fft.irfft(out_spec, n=n_fft, axis=1).astype(np.float32)
    frames_time *= window

    total = (out_frames - 1) * hop + n_fft
    out = np.zeros(total, dtype=np.float32)
    norm = np.zeros(total, dtype=np.float32)
    win_sq = (window * window).astype(np.float32)
    for k in range(out_frames):
        start = k * hop
        out[start:start + n_fft] += frames_time[k]
        norm[start:start + n_fft] += win_sq
    out /= np.maximum(norm, 1e-6)

    start = pad
    result = out[start:start + out_len]
    if result.shape[0] < out_len:
        result = np.pad(result, (0, out_len - result.shape[0]))
    return result.astype(np.float32)
