"""oram.ears.prompt_compiler — translates listening reports into engine-specific prompts.

the prompt compiler is the bridge between listening and generation.
the same source sound can produce radically different outputs depending on engine.
"""

from __future__ import annotations

from oram.ears.routes import ListeningReport


def compile_sfx_prompt(report: ListeningReport) -> str:
    """compile a prompt optimized for ElevenLabs SFX engine.

    focuses on: material, gesture, space, transient detail.
    """
    parts = []
    tech = report.technical
    desc = report.descriptive
    spec = report.speculative

    # recording proximity
    if tech.recording_quality:
        parts.append("Close-mic sound effect of")
    else:
        parts.append("Sound effect of")

    # material description
    if desc.resembles:
        parts.append(desc.resembles)
    elif desc.material:
        parts.append(desc.material)
    else:
        parts.append("unidentified material event")

    # gesture / action
    if desc.action:
        parts.append(f", {desc.action}")

    # texture details
    texture_details = []
    if tech.texture:
        texture_details.append(f"{tech.texture}")
    if tech.noise_balance == "noisy":
        texture_details.append("friction")
    if tech.transient_type:
        texture_details.append(f"{tech.transient_type} transients")
    if texture_details:
        parts.append(f", {' '.join(texture_details)}")

    # space / environment
    if desc.environment:
        parts.append(f", {desc.environment}")

    # speculative flavor (subtle)
    if spec.imaginary_thing:
        parts.append(f", slightly {spec.imaginary_thing.split(',')[0].strip()}")

    # duration
    if tech.duration:
        parts.append(f", {tech.duration} seconds")

    return " ".join(parts).replace("  ", " ").strip()


def compile_voice_prompt(report: ListeningReport) -> str:
    """compile a prompt optimized for ElevenLabs Voice engine.

    focuses on: vocal texture, breath, phonetics, delivery.
    """
    parts = []
    tech = report.technical
    spec = report.speculative

    if spec.sonic_fiction:
        parts.append(spec.sonic_fiction)
    elif spec.hidden_body:
        parts.append(f"A voice shaped by {spec.hidden_body}")
    else:
        parts.append("Whispered asemic vocal texture")

    if tech.texture:
        parts.append(f"with {tech.texture} qualities")

    if tech.is_noisy:
        parts.append(", breath and friction, dry mouth sounds")
    else:
        parts.append(", clean sustained tones, soft delivery")

    parts.append(", no semantic words, pure sonic gesture")

    return " ".join(parts).replace("  ", " ").strip()


def compile_music_prompt(report: ListeningReport) -> str:
    """compile a prompt optimized for ElevenLabs Music engine.

    focuses on: tonality, rhythm, atmosphere, structure.
    """
    parts = []
    tech = report.technical
    desc = report.descriptive
    spec = report.speculative

    parts.append("Create a short instrumental")

    # style inference
    if tech.rhythm == "regular":
        parts.append("rhythmic piece")
    elif tech.pitch_tendency == "tonal":
        parts.append("ambient drone")
    else:
        parts.append("ambient loop")

    # material
    if desc.resembles:
        parts.append(f"inspired by {desc.resembles}")
    elif desc.material:
        parts.append(f"based on {desc.material} resonance")

    # texture
    if tech.texture:
        parts.append(f", {tech.texture} texture")
    if tech.density:
        parts.append(f", {tech.density}")

    # speculative atmosphere
    if spec.impossible_room:
        parts.append(f", {spec.impossible_room}")
    elif desc.environment:
        parts.append(f", {desc.environment}")

    parts.append(", no drums unless rhythmic source")

    return " ".join(parts).replace("  ", " ").strip()


def compile_prompt(report: ListeningReport, engine: str) -> str:
    """compile engine-specific prompt from a listening report.

    engine: "sfx" | "voice" | "music"
    """
    compilers = {
        "sfx": compile_sfx_prompt,
        "voice": compile_voice_prompt,
        "music": compile_music_prompt,
    }
    compiler = compilers.get(engine, compile_sfx_prompt)
    return compiler(report)
