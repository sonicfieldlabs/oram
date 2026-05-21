"""oram.gateway.scribe — ElevenLabs Scribe v2 transcription adapter.

used for agentic listening: speech detection, event tagging,
phonetic extraction, and timestamp generation.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np

from oram.gateway.client import ElevenLabsHTTPClient
from oram_security.credentials import resolve_provider_secret

API_BASE = "https://api.elevenlabs.io/v1"


@dataclass
class ScribeResult:
    """result from transcription."""

    text: str = ""
    words: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)
    contains_speech: bool = False
    contains_voice: bool = False
    language: str = ""


class ScribeAdapter:
    """transcribes and analyzes audio using ElevenLabs Scribe v2."""

    engine_name = "scribe"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or resolve_provider_secret("elevenlabs") or ""

    def is_available(self) -> bool:
        return bool(self._api_key)

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        params: dict | None = None,
    ) -> ScribeResult:
        """transcribe audio and extract events.

        params:
            language_code: str (default: auto-detect)
            tag_audio_events: bool (default: True)
            diarize: bool (default: False)
        """
        import soundfile as sf

        params = params or {}

        # encode to WAV
        buf = io.BytesIO()
        mono = np.mean(audio, axis=1) if audio.ndim > 1 else audio
        sf.write(buf, mono, sample_rate, format="WAV", subtype="PCM_16")
        buf.seek(0)

        form_data = {
            "model_id": "scribe_v2",
            "tag_audio_events": str(params.get("tag_audio_events", True)).lower(),
            "diarize": str(params.get("diarize", False)).lower(),
        }
        if "language_code" in params:
            form_data["language_code"] = params["language_code"]

        client = ElevenLabsHTTPClient(self._api_key, timeout=60.0)
        response = client.post_multipart(
            "/speech-to-text",
            files={"file": ("audio.wav", buf, "audio/wav")},
            data=form_data,
        )

        data = response.json()
        return self._parse_response(data)

    def _parse_response(self, data: dict) -> ScribeResult:
        """parse Scribe API response into ScribeResult."""
        result = ScribeResult()
        result.text = data.get("text", "")
        result.language = data.get("language_code", "")

        # word-level timestamps
        for word_info in data.get("words", []):
            result.words.append({
                "text": word_info.get("text", ""),
                "start": word_info.get("start", 0),
                "end": word_info.get("end", 0),
                "type": word_info.get("type", "word"),
            })

        # audio events
        for event in data.get("words", []):
            if event.get("type") == "audio_event":
                result.events.append({
                    "text": event.get("text", ""),
                    "start": event.get("start", 0),
                    "end": event.get("end", 0),
                })

        # determine speech/voice presence
        result.contains_speech = bool(result.text.strip())
        result.contains_voice = result.contains_speech or any(
            e.get("text", "").lower() in ("singing", "humming", "vocalizing")
            for e in result.events
        )

        return result
