"""oram.stt.whisper_local — local whisper STT adapter.

uses the openai-whisper package.  model defaults to 'small' for better
transcription of poetic phrasing.  respects ORAM_WHISPER_MODEL env var.

LAZY LOAD (§1.10): model is loaded on first transcribe() call, not at
import time, so cold start is fast even when whisper is selected.
"""

from __future__ import annotations

import os

import numpy as np


def _detect_device() -> tuple[str, bool]:
    """detect best available compute device and fp16 capability."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", True
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps", False  # MPS doesn't support fp16 for whisper well
    except ImportError:
        pass
    return "cpu", False


class WhisperLocalAdapter:
    """local whisper speech-to-text adapter with lazy model loading."""

    def __init__(self, model_name: str | None = None):
        """initialize whisper adapter (does NOT load model yet).

        model_name: 'tiny', 'base', 'small', 'medium'
        - tiny:   ~1GB RAM, fastest, lowest accuracy
        - base:   ~1GB RAM, good balance for commands
        - small:  ~2GB RAM, better accuracy (default)
        - medium: ~5GB RAM, high accuracy (not recommended for realtime)
        """
        self._model_name = (
            model_name
            or os.environ.get("ORAM_WHISPER_MODEL", "small")
        )
        self._model = None  # lazy-loaded on first transcribe()
        self._device: str | None = None
        self._fp16: bool | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _ensure_loaded(self) -> None:
        """load the whisper model if not already loaded."""
        if self._model is not None:
            return

        try:
            import whisper
        except ImportError:
            raise ImportError(
                "whisper not installed. install with: pip install openai-whisper\n"
                "or run oram with --no-stt for keyboard-only mode."
            )

        self._device, self._fp16 = _detect_device()
        self._model = whisper.load_model(self._model_name, device=self._device)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """transcribe audio to text using local whisper.

        audio: numpy array, mono or stereo
        sample_rate: sample rate of the audio
        """
        self._ensure_loaded()
        import whisper

        # whisper expects mono float32 at 16kHz
        if audio.ndim > 1:
            mono = np.mean(audio, axis=1)
        else:
            mono = audio

        mono = mono.astype(np.float32)

        # resample to 16kHz if needed
        if sample_rate != 16000:
            from scipy.signal import resample
            new_length = int(len(mono) * 16000 / sample_rate)
            mono = resample(mono, new_length).astype(np.float32)

        # pad/trim to 30 seconds (whisper's expected input length)
        mono = whisper.pad_or_trim(mono)

        # transcribe
        result = self._model.transcribe(
            mono,
            language="en",
            fp16=self._fp16,
        )

        text = result.get("text", "").strip()
        return text
