"""oram.engines.capabilities — core type definitions for the capability-based engine system.

the engine system routes by what the sound IS (capability), not by who provides it.
AudioCapability defines what an engine can do.
SonicIntent defines what the user wants.
the INTENT_CAPABILITY_MAP bridges the two.
"""

from __future__ import annotations

from enum import Enum


class AudioCapability(str, Enum):
    """what an audio engine can do."""

    TEXT_TO_SPEECH = "text_to_speech"
    SPEECH_TO_SPEECH = "speech_to_speech"
    TEXT_TO_SOUND_EFFECT = "text_to_sound_effect"
    TEXT_TO_MUSIC = "text_to_music"
    AUDIO_TO_AUDIO = "audio_to_audio"
    AUDIO_INPAINTING = "audio_inpainting"
    AUDIO_ANALYSIS = "audio_analysis"
    SPEECH_TO_TEXT = "speech_to_text"
    VOICE_DESIGN = "voice_design"
    VOICE_ISOLATION = "voice_isolation"


class EngineMode(str, Enum):
    """how an engine runs."""

    CLOUD = "cloud"
    LOCAL = "local"
    HYBRID = "hybrid"


class EngineProvider(str, Enum):
    """who provides the engine."""

    ELEVENLABS = "elevenlabs"
    STABILITY = "stability"
    HUGGINGFACE = "huggingface"
    FAL = "fal"
    REPLICATE = "replicate"
    LOCAL = "local"


class SonicIntent(str, Enum):
    """what the user wants to DO — the creative intention.

    the user thinks in intents: "i want a voice", "make a texture".
    ORAM translates that into the capabilities needed.
    """

    VOICE = "voice"
    SOUND_EFFECT = "sound_effect"
    MUSIC = "music"
    TEXTURE = "texture"
    TRANSFORM = "transform"
    ANALYZE = "analyze"


# intent → required capabilities (any one match is sufficient)
INTENT_CAPABILITY_MAP: dict[SonicIntent, list[AudioCapability]] = {
    SonicIntent.VOICE: [
        AudioCapability.TEXT_TO_SPEECH,
    ],
    SonicIntent.SOUND_EFFECT: [
        AudioCapability.TEXT_TO_SOUND_EFFECT,
    ],
    SonicIntent.MUSIC: [
        AudioCapability.TEXT_TO_MUSIC,
    ],
    SonicIntent.TEXTURE: [
        AudioCapability.TEXT_TO_SOUND_EFFECT,
        AudioCapability.TEXT_TO_MUSIC,
    ],
    SonicIntent.TRANSFORM: [
        AudioCapability.AUDIO_TO_AUDIO,
        AudioCapability.SPEECH_TO_SPEECH,
    ],
    SonicIntent.ANALYZE: [
        AudioCapability.AUDIO_ANALYSIS,
        AudioCapability.SPEECH_TO_TEXT,
    ],
}


# legacy engine name → sonic intent (backward compat with v1 "sfx"/"voice"/"music")
LEGACY_ENGINE_INTENT_MAP: dict[str, SonicIntent] = {
    "sfx": SonicIntent.SOUND_EFFECT,
    "voice": SonicIntent.VOICE,
    "music": SonicIntent.MUSIC,
}
