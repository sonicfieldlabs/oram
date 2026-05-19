"""oram.command.grammar — text normalization, entity extraction, and rule matching.

implements the deterministic command parser:
1. normalize text
2. extract layer references
3. extract durations
4. match rules
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from oram.command.schemas import (
    AnalyzeMixAction,
    ApplyEffectAction,
    ClearLayerAction,
    EffectParameters,
    ExportMixAction,
    GenerateLayerAction,
    MuteLayerAction,
    OramAction,
    OverdubAction,
    QuitAction,
    RecordAction,
    SaveSessionAction,
    SelectLayerAction,
    SetModeAction,
    SetPanAction,
    SetVolumeAction,
    SoloLayerAction,
    StopRecordingAction,
    UnknownAction,
)

# word-to-number mapping for layer references and durations
WORD_NUMBERS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "sixteen": 16, "twenty": 20, "thirty": 30, "sixty": 60,
    "half": 0,  # special: "half speed"
}
NUMBER_WORD_PATTERN = "|".join(sorted(WORD_NUMBERS, key=len, reverse=True))


@dataclass
class ExtractedEntities:
    """entities extracted from normalized text."""

    layer: int | None = None
    duration_seconds: float | None = None
    bars: int | None = None
    semitones: float | None = None
    raw: str = ""


def normalize(text: str) -> str:
    """normalize command text: lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s.-]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def extract_entities(text: str) -> ExtractedEntities:
    """extract layer references, durations, and semitones from normalized text."""
    entities = ExtractedEntities(raw=text)

    # extract layer reference
    layer_match = re.search(r"layer\s+(\w+)", text)
    if layer_match:
        val = layer_match.group(1)
        if val.isdigit():
            entities.layer = int(val)
        elif val in WORD_NUMBERS:
            entities.layer = WORD_NUMBERS[val]

    # extract duration in seconds
    dur_match = re.search(r"(\w+)\s+seconds?", text)
    if dur_match:
        val = dur_match.group(1)
        if val.isdigit():
            entities.duration_seconds = float(val)
        elif val in WORD_NUMBERS:
            entities.duration_seconds = float(WORD_NUMBERS[val])

    # extract bars
    bar_match = re.search(r"(\w+)\s+bars?", text)
    if bar_match:
        val = bar_match.group(1)
        if val.isdigit():
            entities.bars = int(val)
        elif val in WORD_NUMBERS:
            entities.bars = WORD_NUMBERS[val]

    # extract semitones
    semi_match = re.search(r"(\w+)\s+semitones?", text)
    if semi_match:
        val = semi_match.group(1)
        if val.isdigit():
            entities.semitones = float(val)
        elif val in WORD_NUMBERS:
            entities.semitones = float(WORD_NUMBERS[val])

    # detect "up" / "down" for pitch direction
    if entities.semitones is not None:
        if "down" in text:
            entities.semitones = -abs(entities.semitones)
        else:
            entities.semitones = abs(entities.semitones)
    elif "down" in text and entities.semitones is None:
        # "pitch it down" without explicit semitones
        entities.semitones = -2.0
    elif "pitch" in text and "up" in text and entities.semitones is None:
        entities.semitones = 2.0

    return entities


def _target(entities: ExtractedEntities) -> int | str:
    """resolve target: layer number or 'selected'."""
    return entities.layer if entities.layer is not None else "selected"


def _number_from_token(token: str) -> float | None:
    """parse a numeric token or known number word."""
    token = token.strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        if token in WORD_NUMBERS:
            return float(WORD_NUMBERS[token])
    return None


# --- rule matchers ---
# each returns an action or None


def _match_transport(text: str, entities: ExtractedEntities) -> OramAction | None:
    """match transport commands."""
    if re.match(r"stop\s+record", text):
        return StopRecordingAction()

    if re.match(r"(record|rec)\b", text):
        return RecordAction(
            target=_target(entities),
            duration=entities.duration_seconds,
            bars=entities.bars,
        )

    if re.match(r"overdub", text):
        return OverdubAction(target=_target(entities), duration=entities.duration_seconds)

    if re.match(r"loop\s+this", text):
        return SetModeAction(mode="loop")

    if re.match(r"(select|switch\s+to)\s+layer", text):
        if entities.layer is not None:
            return SelectLayerAction(target=entities.layer)

    if re.match(r"mute", text):
        return MuteLayerAction(target=_target(entities))

    if re.match(r"unmute", text):
        return MuteLayerAction(target=_target(entities))

    if re.match(r"solo", text):
        return SoloLayerAction(target=_target(entities))

    if re.match(r"clear", text):
        return ClearLayerAction(target=_target(entities))

    if re.match(r"save", text):
        return SaveSessionAction()

    if re.match(r"export", text):
        return ExportMixAction()

    if re.match(r"quit|exit|bye", text):
        return QuitAction()

    return None


def _match_mix(text: str, entities: ExtractedEntities) -> OramAction | None:
    """match volume and pan commands."""
    target = _target(entities)
    number = rf"(-?\d+(?:\.\d+)?|{NUMBER_WORD_PATTERN})"

    volume_match = re.search(
        rf"\b(?:set\s+)?(?:volume|gain)\b(?:\s+layer\s+\w+)?\s+{number}\b",
        text,
    )
    if volume_match:
        value = _number_from_token(volume_match.group(1))
        if value is None:
            return None
        # Spoken "50" is treated as 50%; web sliders send 0.0-2.0 directly.
        volume = value / 100.0 if value > 2.0 else value
        return SetVolumeAction(target=target, volume=volume)

    pan_match = re.search(
        rf"\b(?:set\s+)?pan\b(?:\s+layer\s+\w+)?\s+{number}\b",
        text,
    )
    if pan_match:
        value = _number_from_token(pan_match.group(1))
        if value is None:
            return None
        return SetPanAction(target=target, pan=max(-1.0, min(1.0, value)))

    if "pan" in text and "left" in text:
        return SetPanAction(target=target, pan=-0.75)

    if "pan" in text and "right" in text:
        return SetPanAction(target=target, pan=0.75)

    if "pan" in text and ("center" in text or "centre" in text):
        return SetPanAction(target=target, pan=0.0)

    return None


def _match_transform(text: str, entities: ExtractedEntities) -> OramAction | None:
    """match transform / DSP commands."""
    target = _target(entities)

    if re.search(r"\breverse\b", text):
        return ApplyEffectAction(target=target, effect="reverse")

    if re.search(r"\bslower\b", text):
        return ApplyEffectAction(
            target=target, effect="speed",
            parameters=EffectParameters(speed=0.5),
        )

    if re.search(r"\bfaster\b", text):
        return ApplyEffectAction(
            target=target, effect="speed",
            parameters=EffectParameters(speed=2.0),
        )

    if re.search(r"\bhalf\s+speed\b", text):
        return ApplyEffectAction(
            target=target, effect="speed",
            parameters=EffectParameters(speed=0.5),
        )

    if re.search(r"\bdouble\s+speed\b", text):
        return ApplyEffectAction(
            target=target, effect="speed",
            parameters=EffectParameters(speed=2.0),
        )

    if re.search(r"\bpitch\b", text):
        semitones = entities.semitones if entities.semitones is not None else -2.0
        return ApplyEffectAction(
            target=target, effect="pitch",
            parameters=EffectParameters(semitones=semitones),
        )

    if re.search(r"\bfade\s+(the\s+)?end\b|fade\s+out\b", text):
        return ApplyEffectAction(target=target, effect="fade_out")

    if re.search(r"\bfade\s+in\b|fade\s+(the\s+)?start\b|fade\s+(the\s+)?beginning\b", text):
        return ApplyEffectAction(target=target, effect="fade_in")

    if re.search(r"\btrim\s+(the\s+)?(beginning|start)\b", text):
        return ApplyEffectAction(target=target, effect="trim_start")

    if re.search(r"\btrim\s+(the\s+)?end\b", text):
        return ApplyEffectAction(target=target, effect="trim_end")

    if re.search(r"\bdarker\b|filter\s+the\s+voice\b", text):
        return ApplyEffectAction(target=target, effect="lowpass")

    if re.search(r"\bthinner\b", text):
        return ApplyEffectAction(target=target, effect="highpass")

    if re.search(r"\bgranulat\w*\b", text):
        density = 0.3
        jitter = 0.15
        if "softly" in text or "soft" in text:
            density = 0.3
            jitter = 0.15
        elif "dust" in text or "heavy" in text:
            density = 0.7
            jitter = 0.5
        return ApplyEffectAction(
            target=target, effect="granular",
            parameters=EffectParameters(density=density, jitter=jitter),
        )

    if re.search(r"\bstretch\b.*\bbreath\w*\b", text):
        return ApplyEffectAction(target=target, effect="stretch_breathe")

    if re.search(r"\bturn\s+into\s+dust\b", text):
        return ApplyEffectAction(
            target=target, effect="granular",
            parameters=EffectParameters(density=0.7, jitter=0.5),
        )

    return None


def _match_spatial(text: str, entities: ExtractedEntities) -> OramAction | None:
    """match spatial / reverb commands."""
    target = _target(entities)

    if re.search(r"\bfar\s+away\b|\bdistance\b", text):
        return ApplyEffectAction(target=target, effect="spatial_far")

    if re.search(r"\bsmall\s+room\b", text):
        return ApplyEffectAction(
            target=target, effect="reverb",
            parameters=EffectParameters(decay="short"),
        )

    if re.search(r"\bwash\b.*\breverb\b|\bdrench\b.*\breverb\b", text):
        return ApplyEffectAction(
            target=target, effect="reverb",
            parameters=EffectParameters(wet=0.8),
        )

    if re.search(r"\bnarrow\w*\b", text):
        return ApplyEffectAction(
            target=target, effect="reverb",
            parameters=EffectParameters(narrow=True),
        )

    if re.search(r"\breverb\b|\broom\b", text):
        return ApplyEffectAction(
            target=target, effect="reverb",
            parameters=EffectParameters(wet=0.4),
        )

    return None


def _match_generative(text: str, entities: ExtractedEntities) -> OramAction | None:
    """match generative / summon commands."""
    gen_patterns = [
        r"^add\s+(?:a\s+)?(.+)",
        r"^create\s+(?:a\s+)?(.+)",
        r"^summon\s+(?:a\s+)?(.+)",
        r"^generate\s+(?:a\s+)?(.+)",
        r"^make\s+(?:a\s+)?(?:fake\s+)?(.+recording\w*|.+ambien\w+|.+drone\w*|.+tone\w*|.+field\w*.*)",
    ]

    for pattern in gen_patterns:
        match = re.match(pattern, text)
        if match:
            prompt = match.group(1).strip()
            # don't match transform or spatial phrases as generative
            reject_words = [
                "slower", "faster", "darker", "thinner", "softer",
                "distance", "far away", "narrow",
            ]
            # allow generative prompts with sound-related words even if
            # they contain 'room' or 'reverb' (e.g. 'room tone')
            sound_words = ["tone", "drone", "rain", "noise", "ambien", "forest",
                           "machine", "recording", "field"]
            has_sound_word = any(w in prompt for w in sound_words)
            if any(word in prompt for word in reject_words):
                return None
            if not has_sound_word and any(w in prompt for w in ["reverb", "room"]):
                return None
            return GenerateLayerAction(prompt=prompt)

    return None


def _match_listening(text: str, entities: ExtractedEntities) -> OramAction | None:
    """match listening / analysis commands."""
    if re.search(
        r"\blisten\s+to\b|\bdescribe\b|\bwhat\s+is\b|\bwhat\s+changed\b"
        r"|\bfind\s+speech\b|\banalyze\b|\banalysis\b",
        text,
    ):
        target = entities.layer if entities.layer is not None else None
        focus = None
        if "dense" in text or "density" in text:
            focus = "density"
        elif "speech" in text:
            focus = "speech"
        elif "changed" in text:
            focus = "changes"
        return AnalyzeMixAction(target=target, focus=focus)

    return None


def _match_mode(text: str, _entities: ExtractedEntities) -> OramAction | None:
    """match mode-setting commands."""
    if re.search(r"\bsofter\b|\bquieter\b", text):
        # "make everything softer" is a volume reduction, not a mode change
        return ApplyEffectAction(
            target="all", effect="fade_out",
            parameters=EffectParameters(fade_seconds=0.0),
        )
    return None


# ── sound-vocabulary catch-all ──
# when no deterministic rule matches, treat descriptive text as a generation
# command if it contains sound-related vocabulary or is long enough to be
# a descriptive prompt.

_SOUND_VOCABULARY = frozenset({
    # textures & materials
    "ambient", "ambience", "ambiance", "drone", "noise", "tone", "pad",
    "texture", "resonance", "oscillation", "pulse", "glitch", "static",
    "feedback", "echo", "shimmer", "ring", "rattle", "scrape", "click",
    "pop", "hiss", "whoosh", "sweep", "loop", "beat", "rhythm", "chord",
    "bass", "treble", "sub", "frequency", "wave", "signal", "hum", "buzz",
    "crackle", "rumble", "thunder", "whisper", "breath", "murmur",
    # natural
    "rain", "wind", "water", "ocean", "forest", "fire", "storm", "bird",
    "insect", "cricket", "river", "stream", "waves", "ice", "snow",
    # materials
    "metal", "glass", "wood", "stone", "concrete", "plastic", "rubber",
    "paper", "fabric", "skin", "bone", "ceramic", "crystal", "steel",
    "iron", "copper", "aluminum", "wire", "string", "membrane",
    # qualities
    "dark", "bright", "warm", "cold", "deep", "soft", "harsh", "gentle",
    "heavy", "light", "thick", "thin", "wet", "dry", "raw", "smooth",
    "rough", "sharp", "dull", "muffled", "crisp", "muddy", "clean",
    "dirty", "distorted", "lo-fi", "lofi", "saturated", "ethereal",
    "dreamy", "haunting", "eerie", "ominous", "peaceful", "chaotic",
    "minimal", "maximal", "sparse", "dense", "hollow", "full",
    # domains
    "industrial", "organic", "synthetic", "mechanical", "digital",
    "analog", "electronic", "acoustic", "electric", "atmospheric",
    "cinematic", "filmic", "spatial", "underwater", "subterranean",
    "aerial", "celestial", "nocturnal", "urban", "rural",
    # technical audio
    "granular", "spectral", "harmonic", "subharmonic", "overtone",
    "fundamental", "formant", "filtered", "modulated", "detuned",
    "bitcrushed", "compressed", "saturated", "clipped",
    # instruments & sources
    "synth", "synthesizer", "piano", "guitar", "violin", "cello",
    "flute", "bell", "gong", "cymbal", "drum", "percussion",
    "machine", "engine", "motor", "generator", "turbine",
    "recording", "field", "foley", "soundscape", "landscape",
})

# words that indicate this is a system command, not a sound prompt
_SYSTEM_WORDS = frozenset({
    "select", "mute", "unmute", "solo", "unsolo", "volume", "gain",
    "pan", "clear", "delete", "remove", "save", "export", "quit",
    "exit", "stop", "record", "overdub", "set", "mode", "help",
    "undo", "redo", "settings", "device", "kill", "make", "loop",
})


def _match_sound_prompt(text: str, entities: ExtractedEntities) -> OramAction | None:
    """catch-all: treat descriptive text as a sound generation prompt.

    accepts text that:
    - contains at least one sound-vocabulary word, OR
    - is at least 5 words long (assumes it's a descriptive prompt)

    rejects text that:
    - is very short (< 3 words) with no sound vocabulary
    - starts with a system-command word
    """
    words = text.split()
    if not words:
        return None

    # reject if the first word is a system command
    if words[0] in _SYSTEM_WORDS:
        return None

    # reject single words — too ambiguous
    if len(words) < 2:
        return None

    # check for sound vocabulary
    text_words = set(words)
    sound_hits = text_words & _SOUND_VOCABULARY
    has_sound_word = len(sound_hits) > 0

    # accept if sound vocabulary detected
    if has_sound_word:
        return GenerateLayerAction(prompt=text.strip())

    # accept long descriptive text (5+ words) as a prompt
    if len(words) >= 5:
        return GenerateLayerAction(prompt=text.strip())

    return None


def match_rules(text: str) -> OramAction:
    """run the full deterministic rule chain on normalized text.

    returns the first matching action, or UnknownAction if nothing matches.
    """
    normalized = normalize(text)
    entities = extract_entities(normalized)

    # priority order: generative before spatial to prevent 'room tone'
    # from matching spatial's generic 'room' rule.
    # sound-prompt catch-all is last before unknown.
    matchers = [
        _match_transport,
        _match_mix,
        _match_transform,
        _match_generative,
        _match_spatial,
        _match_listening,
        _match_mode,
        _match_sound_prompt,
    ]

    for matcher in matchers:
        result = matcher(normalized, entities)
        if result is not None:
            return result

    return UnknownAction(reason="no rule matched", raw_text=text)
