"""oram.engines.elevenlabs — ElevenLabs engine adapters wrapped for the ORAM engine system.

each adapter wraps an existing gateway/ adapter as an OramEngineAdapter,
delegating all HTTP/API logic to the battle-tested gateway code.
no HTTP rewrites — just protocol bridging.

engines:
- ElevenLabsSFXEngine: text → sound effects
- ElevenLabsVoiceEngine: text → speech, speech → speech
- ElevenLabsMusicEngine: text → music
- ElevenLabsScribeEngine: audio → text, audio → analysis
"""

from __future__ import annotations

import logging

import numpy as np

from oram.engines.adapter import EngineSpec, GenerationRequest, GenerationResult
from oram.engines.capabilities import AudioCapability, EngineMode, EngineProvider

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SFX
# ---------------------------------------------------------------------------

class ElevenLabsSFXEngine:
    """wraps gateway.sfx.SFXAdapter as an OramEngineAdapter."""

    spec = EngineSpec(
        id="elevenlabs-sfx",
        provider=EngineProvider.ELEVENLABS,
        label="ElevenLabs Sound Effects",
        mode=EngineMode.CLOUD,
        capabilities=[AudioCapability.TEXT_TO_SOUND_EFFECT],
        requires_api_key=True,
        supports_streaming=False,
        supports_seed=False,
        supports_audio_input=False,
        max_duration_seconds=22.0,
        cost_per_second=40.0,
        latency_profile="medium",
    )

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._adapter = None

    def _get_adapter(self):
        if self._adapter is None:
            from oram.gateway.sfx import SFXAdapter
            self._adapter = SFXAdapter(api_key=self._api_key)
        return self._adapter

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        adapter = self._get_adapter()
        params = request.to_adapter_params()
        result = adapter.generate(request.prompt, params)
        return GenerationResult(
            audio=result.audio,
            sample_rate=result.sample_rate,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=result.prompt_used,
            duration_seconds=result.duration_seconds,
            cost_credits=result.cost_credits,
            cost_currency="credits",
            parameters=result.parameters,
        )


# ---------------------------------------------------------------------------
# Voice (TTS + Speech-to-Speech)
# ---------------------------------------------------------------------------

class ElevenLabsVoiceEngine:
    """wraps gateway.voice.VoiceAdapter as an OramEngineAdapter."""

    spec = EngineSpec(
        id="elevenlabs-tts",
        provider=EngineProvider.ELEVENLABS,
        label="ElevenLabs Voice",
        mode=EngineMode.CLOUD,
        capabilities=[
            AudioCapability.TEXT_TO_SPEECH,
            AudioCapability.SPEECH_TO_SPEECH,
        ],
        requires_api_key=True,
        supports_streaming=True,
        supports_seed=False,
        supports_audio_input=True,
        max_duration_seconds=300.0,
        cost_per_second=30.0,
        latency_profile="fast",
    )

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._adapter = None

    def _get_adapter(self):
        if self._adapter is None:
            from oram.gateway.voice import VoiceAdapter
            self._adapter = VoiceAdapter(api_key=self._api_key)
        return self._adapter

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        adapter = self._get_adapter()

        # speech-to-speech if source audio is provided
        if request.source_audio is not None:
            params = request.to_adapter_params()
            result = adapter.speech_to_speech(
                source_audio=request.source_audio,
                sample_rate=request.source_sample_rate,
                params=params,
            )
            return GenerationResult(
                audio=result.audio,
                sample_rate=result.sample_rate,
                engine_id=self.spec.id,
                provider=self.spec.provider.value,
                prompt_used=result.prompt_used,
                duration_seconds=result.duration_seconds,
                cost_credits=result.cost_credits,
                cost_currency="credits",
                parameters=result.parameters,
                metadata={"mode": "speech_to_speech"},
            )

        # text-to-speech
        params = request.to_adapter_params()
        result = adapter.generate(request.prompt, params)
        return GenerationResult(
            audio=result.audio,
            sample_rate=result.sample_rate,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=result.prompt_used,
            duration_seconds=result.duration_seconds,
            cost_credits=result.cost_credits,
            cost_currency="credits",
            parameters=result.parameters,
            metadata={"mode": "text_to_speech"},
        )


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------

class ElevenLabsMusicEngine:
    """wraps gateway.music.MusicAdapter as an OramEngineAdapter."""

    spec = EngineSpec(
        id="elevenlabs-music",
        provider=EngineProvider.ELEVENLABS,
        label="ElevenLabs Music",
        mode=EngineMode.CLOUD,
        capabilities=[AudioCapability.TEXT_TO_MUSIC],
        requires_api_key=True,
        supports_streaming=True,
        supports_seed=False,
        supports_audio_input=False,
        max_duration_seconds=600.0,
        cost_per_second=50.0,
        latency_profile="slow",
    )

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._adapter = None

    def _get_adapter(self):
        if self._adapter is None:
            from oram.gateway.music import MusicAdapter
            self._adapter = MusicAdapter(api_key=self._api_key)
        return self._adapter

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        adapter = self._get_adapter()
        params = request.to_adapter_params()
        result = adapter.generate(request.prompt, params)
        return GenerationResult(
            audio=result.audio,
            sample_rate=result.sample_rate,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=result.prompt_used,
            duration_seconds=result.duration_seconds,
            cost_credits=result.cost_credits,
            cost_currency="credits",
            parameters=result.parameters,
        )


# ---------------------------------------------------------------------------
# Scribe (STT + Audio Analysis)
# ---------------------------------------------------------------------------

class ElevenLabsScribeEngine:
    """wraps gateway.scribe.ScribeAdapter as an OramEngineAdapter.

    unlike generative engines, scribe produces text, not audio.
    the generate() method returns a zero-length audio buffer with
    transcription results in the metadata field.
    """

    spec = EngineSpec(
        id="elevenlabs-scribe",
        provider=EngineProvider.ELEVENLABS,
        label="ElevenLabs Scribe",
        mode=EngineMode.CLOUD,
        capabilities=[
            AudioCapability.SPEECH_TO_TEXT,
            AudioCapability.AUDIO_ANALYSIS,
        ],
        requires_api_key=True,
        supports_streaming=False,
        supports_seed=False,
        supports_audio_input=True,
        max_duration_seconds=3600.0,
        cost_per_second=10.0,
        latency_profile="medium",
    )

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._adapter = None

    def _get_adapter(self):
        if self._adapter is None:
            from oram.gateway.scribe import ScribeAdapter
            self._adapter = ScribeAdapter(api_key=self._api_key)
        return self._adapter

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """for scribe, 'generate' means 'transcribe'.

        requires source_audio. returns empty audio buffer with
        transcription in metadata.
        """
        if request.source_audio is None:
            raise ValueError("scribe engine requires source_audio in the request")

        adapter = self._get_adapter()
        params = request.to_adapter_params()
        scribe_result = adapter.transcribe(
            audio=request.source_audio,
            sample_rate=request.source_sample_rate,
            params=params,
        )

        return GenerationResult(
            audio=np.zeros((0, 2), dtype=np.float32),
            sample_rate=request.source_sample_rate,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used="[transcription]",
            duration_seconds=0.0,
            cost_credits=0.0,
            metadata={
                "mode": "speech_to_text",
                "text": scribe_result.text,
                "words": scribe_result.words,
                "events": scribe_result.events,
                "speakers": scribe_result.speakers,
                "contains_speech": scribe_result.contains_speech,
                "contains_voice": scribe_result.contains_voice,
                "language": scribe_result.language,
            },
        )


# ---------------------------------------------------------------------------
# Voice Changer (dedicated Speech-to-Speech)
# ---------------------------------------------------------------------------

class ElevenLabsVoiceChangerEngine:
    """dedicated speech-to-speech engine for voice transformation.

    while ElevenLabsVoiceEngine handles both TTS and STS,
    this engine is focused purely on voice changing — taking
    source audio and re-voicing it with a different voice identity.
    """

    spec = EngineSpec(
        id="elevenlabs-voice-changer",
        provider=EngineProvider.ELEVENLABS,
        label="ElevenLabs Voice Changer",
        mode=EngineMode.CLOUD,
        capabilities=[AudioCapability.SPEECH_TO_SPEECH],
        requires_api_key=True,
        supports_streaming=False,
        supports_seed=False,
        supports_audio_input=True,
        max_duration_seconds=300.0,
        cost_per_second=35.0,
        latency_profile="medium",
    )

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._adapter = None

    def _get_adapter(self):
        if self._adapter is None:
            from oram.gateway.voice import VoiceAdapter
            self._adapter = VoiceAdapter(api_key=self._api_key)
        return self._adapter

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.source_audio is None:
            raise ValueError("voice changer requires source_audio in the request")

        adapter = self._get_adapter()
        params = request.to_adapter_params()
        result = adapter.speech_to_speech(
            source_audio=request.source_audio,
            sample_rate=request.source_sample_rate,
            params=params,
        )
        return GenerationResult(
            audio=result.audio,
            sample_rate=result.sample_rate,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=result.prompt_used,
            duration_seconds=result.duration_seconds,
            cost_credits=result.cost_credits,
            cost_currency="credits",
            parameters=result.parameters,
            metadata={"mode": "voice_changer"},
        )


# ---------------------------------------------------------------------------
# Voice Design (create custom voices from description)
# ---------------------------------------------------------------------------

class ElevenLabsVoiceDesignEngine:
    """create custom voice profiles from text descriptions.

    uses the ElevenLabs Voice Design / Voice Generation API to create
    a new voice from a natural language description. the generated voice
    is returned as a short sample audio clip.

    this is a meta-engine — it produces voice identities, not speech content.
    the resulting voice_id can then be used with ElevenLabsVoiceEngine.
    """

    spec = EngineSpec(
        id="elevenlabs-voice-design",
        provider=EngineProvider.ELEVENLABS,
        label="ElevenLabs Voice Design",
        mode=EngineMode.CLOUD,
        capabilities=[AudioCapability.VOICE_DESIGN],
        requires_api_key=True,
        supports_streaming=False,
        supports_seed=False,
        supports_audio_input=False,
        max_duration_seconds=30.0,
        cost_per_second=50.0,
        latency_profile="slow",
    )

    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """generate a voice preview from a text description.

        the prompt should describe the desired voice characteristics:
        "a warm, breathy female voice with a slight rasp"
        """
        import io

        from oram.gateway.client import ElevenLabsHTTPClient

        body = {
            "text": request.parameters.get(
                "preview_text",
                "The quick brown fox jumps over the lazy dog.",
            ),
            "voice_description": request.prompt,
            "model_id": request.model_id or "eleven_multilingual_v2",
        }

        client = ElevenLabsHTTPClient(self._api_key, timeout=60.0)
        response = client.post_json("/text-to-voice/create-previews", body)

        # parse previews response
        data = response.json()
        previews = data.get("previews", [])
        if not previews:
            return GenerationResult(
                audio=np.zeros((0, 2), dtype=np.float32),
                sample_rate=48000,
                engine_id=self.spec.id,
                provider=self.spec.provider.value,
                prompt_used=request.prompt,
                metadata={"error": "no previews generated"},
            )

        # decode the first preview
        preview = previews[0]
        audio_b64 = preview.get("audio_base64", "")
        generated_voice_id = preview.get("generated_voice_id", "")

        if audio_b64:
            import base64
            import soundfile as sf

            audio_bytes = base64.b64decode(audio_b64)
            buf = io.BytesIO(audio_bytes)
            try:
                audio, sr = sf.read(buf)
                if audio.ndim == 1:
                    audio = np.column_stack([audio, audio])
                audio = audio.astype(np.float32)
            except Exception:
                audio = np.zeros((0, 2), dtype=np.float32)
                sr = 48000
        else:
            audio = np.zeros((0, 2), dtype=np.float32)
            sr = 48000

        return GenerationResult(
            audio=audio,
            sample_rate=sr,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used=request.prompt,
            cost_credits=client.parse_cost_header(response) or 100,
            metadata={
                "mode": "voice_design",
                "generated_voice_id": generated_voice_id,
                "voice_description": request.prompt,
            },
        )


# ---------------------------------------------------------------------------
# Voice Isolation (separate vocals from background)
# ---------------------------------------------------------------------------

class ElevenLabsIsolationEngine:
    """isolate voice from background audio.

    uses the ElevenLabs Audio Isolation API to separate vocal content
    from noise, music, or environmental sounds.
    """

    spec = EngineSpec(
        id="elevenlabs-isolation",
        provider=EngineProvider.ELEVENLABS,
        label="ElevenLabs Voice Isolation",
        mode=EngineMode.CLOUD,
        capabilities=[AudioCapability.VOICE_ISOLATION],
        requires_api_key=True,
        supports_streaming=False,
        supports_seed=False,
        supports_audio_input=True,
        max_duration_seconds=600.0,
        cost_per_second=20.0,
        latency_profile="medium",
    )

    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """isolate voice from source audio.

        requires source_audio. returns the isolated vocal track.
        """
        if request.source_audio is None:
            raise ValueError("voice isolation requires source_audio in the request")

        import io
        import soundfile as sf

        from oram.gateway.client import ElevenLabsHTTPClient

        # encode source to WAV
        buf = io.BytesIO()
        mono = (
            np.mean(request.source_audio, axis=1)
            if request.source_audio.ndim > 1
            else request.source_audio
        )
        sf.write(buf, mono, request.source_sample_rate, format="WAV", subtype="PCM_16")
        buf.seek(0)

        client = ElevenLabsHTTPClient(self._api_key, timeout=120.0)
        response = client.post_multipart(
            "/audio-isolation",
            files={"audio": ("audio.wav", buf, "audio/wav")},
            data={},
        )

        # decode response audio
        audio_buf = io.BytesIO(response.content)
        try:
            audio, sr = sf.read(audio_buf)
            if audio.ndim == 1:
                audio = np.column_stack([audio, audio])
            audio = audio.astype(np.float32)
        except Exception:
            from oram.gateway.base import GenerationFailedError
            raise GenerationFailedError(
                prompt="[voice isolation]",
                status=0,
                body=f"failed to decode {len(response.content)} bytes",
            )

        return GenerationResult(
            audio=audio,
            sample_rate=sr,
            engine_id=self.spec.id,
            provider=self.spec.provider.value,
            prompt_used="[voice isolation]",
            duration_seconds=len(audio) / sr,
            cost_credits=client.parse_cost_header(response) or float(len(mono) / request.source_sample_rate * 20),
            metadata={"mode": "voice_isolation"},
        )

