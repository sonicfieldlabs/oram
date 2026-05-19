"""oram.stt.mock — mock STT adapter for testing."""

from __future__ import annotations

import numpy as np


class MockSTTAdapter:
    """mock STT that returns predetermined or random commands."""

    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or [
            "record eight seconds",
            "reverse layer one",
            "add distant metallic rain",
            "granulate softly",
            "listen to the texture",
        ]
        self._index = 0

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """return the next predetermined response."""
        if not self._responses:
            return ""
        response = self._responses[self._index % len(self._responses)]
        self._index += 1
        return response
