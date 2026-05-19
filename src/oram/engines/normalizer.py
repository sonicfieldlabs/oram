"""oram.engines.normalizer — post-generation audio normalization pipeline.

every engine produces audio in different formats, sample rates, loudness levels.
the normalizer standardizes everything before insertion into the ORAM timeline:

1. resample to target sample rate
2. ensure stereo float32
3. normalize loudness
4. trim leading/trailing silence
5. apply micro fade in/out
"""

from __future__ import annotations

import numpy as np


class AudioNormalizer:
    """normalize all engine outputs before inserting into ORAM timeline."""

    def __init__(
        self,
        target_sr: int = 48000,
        target_lufs: float = -14.0,
        fade_ms: float = 10.0,
        trim_threshold: float = 0.005,
        trim_enabled: bool = True,
    ):
        self._target_sr = target_sr
        self._target_lufs = target_lufs
        self._fade_ms = fade_ms
        self._trim_threshold = trim_threshold
        self._trim_enabled = trim_enabled

    def normalize(
        self,
        audio: np.ndarray,
        source_sr: int,
        target_sr: int | None = None,
    ) -> np.ndarray:
        """full normalization pipeline.

        returns stereo float32 at target sample rate.
        """
        target = target_sr or self._target_sr

        # 1. ensure float32
        audio = np.asarray(audio, dtype=np.float32)

        # 2. ensure stereo
        audio = self._ensure_stereo(audio)

        # 3. resample if needed
        if source_sr != target:
            audio = self._resample(audio, source_sr, target)

        # 4. trim silence
        if self._trim_enabled:
            audio = self._trim_silence(audio)

        # 5. normalize loudness
        audio = self._normalize_loudness(audio)

        # 6. micro fade in/out
        audio = self._apply_fades(audio, target)

        return audio

    def _ensure_stereo(self, audio: np.ndarray) -> np.ndarray:
        """ensure audio is stereo (N, 2)."""
        if audio.ndim == 1:
            return np.column_stack([audio, audio])
        if audio.ndim == 2:
            if audio.shape[1] == 1:
                return np.column_stack([audio[:, 0], audio[:, 0]])
            if audio.shape[1] > 2:
                return audio[:, :2]
        return audio

    def _resample(self, audio: np.ndarray, source_sr: int, target_sr: int) -> np.ndarray:
        """resample audio using scipy.

        falls back to simple linear interpolation if scipy unavailable.
        """
        if source_sr == target_sr:
            return audio

        ratio = target_sr / source_sr
        new_length = int(audio.shape[0] * ratio)

        try:
            from scipy.signal import resample
            resampled = np.zeros((new_length, audio.shape[1]), dtype=np.float32)
            for ch in range(audio.shape[1]):
                resampled[:, ch] = resample(audio[:, ch], new_length).astype(np.float32)
            return resampled
        except ImportError:
            # fallback: linear interpolation
            indices = np.linspace(0, audio.shape[0] - 1, new_length)
            resampled = np.zeros((new_length, audio.shape[1]), dtype=np.float32)
            for ch in range(audio.shape[1]):
                resampled[:, ch] = np.interp(indices, np.arange(audio.shape[0]), audio[:, ch])
            return resampled

    def _trim_silence(self, audio: np.ndarray) -> np.ndarray:
        """trim leading and trailing silence below threshold."""
        if audio.shape[0] == 0:
            return audio

        mono = np.mean(np.abs(audio), axis=1)
        above = np.where(mono > self._trim_threshold)[0]

        if len(above) == 0:
            return audio  # all silence — keep as-is

        start = max(0, above[0] - 64)    # keep tiny pre-onset padding
        end = min(len(mono), above[-1] + 64)
        return audio[start:end]

    def _normalize_loudness(self, audio: np.ndarray) -> np.ndarray:
        """simple RMS-based loudness normalization.

        targets approximately -14 LUFS using RMS as proxy.
        """
        if audio.shape[0] == 0:
            return audio

        rms = np.sqrt(np.mean(audio ** 2))
        if rms < 1e-10:
            return audio

        # target RMS for -14 LUFS is approximately 0.2
        # (simplified — true LUFS needs K-weighting)
        target_rms = 10 ** (self._target_lufs / 20)
        gain = target_rms / rms

        # limit gain to prevent boosting noise floor
        gain = min(gain, 10.0)

        result = audio * gain

        # peak limiting
        peak = np.max(np.abs(result))
        if peak > 0.95:
            result *= 0.95 / peak

        return result

    def _apply_fades(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """apply micro fade in/out to prevent clicks."""
        if audio.shape[0] == 0:
            return audio

        fade_samples = int(self._fade_ms / 1000.0 * sample_rate)
        fade_samples = min(fade_samples, audio.shape[0] // 4)

        if fade_samples < 2:
            return audio

        result = audio.copy()

        # fade in
        fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
        for ch in range(result.shape[1]):
            result[:fade_samples, ch] *= fade_in

        # fade out
        fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
        for ch in range(result.shape[1]):
            result[-fade_samples:, ch] *= fade_out

        return result
