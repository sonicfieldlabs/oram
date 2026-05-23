"""oram.ears.prompt_compiler — translates listening reports into engine-specific prompts.

the prompt compiler is the bridge between listening and generation.
v2: data-driven constraints + creative freedom.

structure of every prompt:
1. hard constraints — pitch, duration, harmonic series, BPM (from analysis data)
2. soft constraints — spectral gaps, key context (from mix context)
3. creative seed — the imaginative direction (from speculative route)
4. negative constraints — no speech, no drums unless rhythmic source
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from oram.ears.routes import ListeningReport

if TYPE_CHECKING:
    from oram.ears.mix_context import MixContext


def compile_sfx_prompt(
    report: ListeningReport,
    mix_context: MixContext | None = None,
) -> str:
    """compile a prompt optimized for sound effects / textures.

    uses concrete data as constraints, speculative layer for creative direction.
    """
    parts = []
    tech = report.technical
    spec = report.speculative

    # ── 1. hard constraints from analysis data ──

    # pitch anchor
    if tech.dominant_pitch_note and tech.pitch_detection_confidence > 0.3:
        parts.append(f"Sound texture rooted at {tech.dominant_pitch_note} ({tech.dominant_pitch_hz:.0f}Hz)")
    elif tech.spectral_centroid_hz > 0:
        # use centroid as rough pitch region when no clear pitch
        if tech.spectral_centroid_hz < 400:
            parts.append("Low-frequency sound texture")
        elif tech.spectral_centroid_hz < 2000:
            parts.append("Mid-range sound texture")
        else:
            parts.append("High-frequency sound texture")
    else:
        parts.append("Sound texture")

    # harmonic character
    if tech.harmonic_ratios and len(tech.harmonic_ratios) > 1:
        if len(tech.harmonic_ratios) >= 5:
            parts.append("with rich harmonic content")
        elif any(r > 3.5 for r in tech.harmonic_ratios):
            parts.append("with upper partials and overtones")
        else:
            parts.append("with simple harmonic structure")

    # rhythmic character
    if tech.estimated_bpm > 0 and tech.bpm_confidence > 0.3:
        parts.append(f"at {tech.estimated_bpm:.0f} BPM")
    if tech.onset_pattern and "x" in tech.onset_pattern:
        density = tech.onset_pattern.count("x")
        if density > 8:
            parts.append("with dense rhythmic activity")
        elif density > 3:
            parts.append("with rhythmic pulse")

    # key context
    if tech.key_estimate and tech.key_confidence > 0.3:
        parts.append(f"in {tech.key_estimate}")

    # envelope shape
    if tech.attack_profile and tech.decay_profile:
        parts.append(f", {tech.attack_profile} attack, {tech.decay_profile}")

    # texture from analysis
    if tech.texture:
        parts.append(f", {tech.texture} texture")

    # ── 2. soft constraints from mix context ──
    if mix_context:
        try:
            from oram.ears.mix_context import format_mix_constraints
            constraint_text = format_mix_constraints(mix_context)
            if constraint_text:
                parts.append(f". {constraint_text}")
        except Exception:
            pass

    # ── 3. creative seed from speculative route ──
    if spec.imaginary_thing:
        parts.append(f". Imagine: {spec.imaginary_thing.split(',')[0].strip()}")
    elif spec.non_human_gesture:
        parts.append(f". Evokes: {spec.non_human_gesture}")

    # ── 4. negative constraints ──
    if tech.duration:
        parts.append(f". {tech.duration} seconds")
    parts.append(", no speech, no vocals")

    return " ".join(parts).replace("  ", " ").strip()


def compile_voice_prompt(
    report: ListeningReport,
    mix_context: MixContext | None = None,
) -> str:
    """ORAM never generates speech — redirect to sfx prompt."""
    return compile_sfx_prompt(report, mix_context)


def compile_music_prompt(
    report: ListeningReport,
    mix_context: MixContext | None = None,
) -> str:
    """compile a prompt optimized for music generation.

    uses concrete data as constraints, speculative layer for atmosphere.
    """
    parts = []
    tech = report.technical
    desc = report.descriptive
    spec = report.speculative

    parts.append("Create a short instrumental")

    # ── 1. hard constraints ──

    # key + pitch
    if tech.key_estimate and tech.key_confidence > 0.3:
        parts.append(f"in {tech.key_estimate}")
    elif tech.dominant_pitch_note and tech.pitch_detection_confidence > 0.3:
        parts.append(f"rooted around {tech.dominant_pitch_note}")

    # tempo
    if tech.estimated_bpm > 0 and tech.bpm_confidence > 0.3:
        parts.append(f"at {tech.estimated_bpm:.0f} BPM")

    # style inference from rhythm
    if tech.rhythm == "dense rhythmic" or tech.rhythm == "regular":
        parts.append("rhythmic piece")
    elif tech.pitch_tendency and "tonal" in tech.pitch_tendency:
        parts.append("ambient drone")
    else:
        parts.append("ambient loop")

    # harmonic content
    if tech.harmonic_ratios and len(tech.harmonic_ratios) >= 4:
        parts.append("with harmonic richness")

    # material from descriptive
    if desc.resembles:
        parts.append(f"inspired by {desc.resembles}")
    elif desc.material:
        parts.append(f"based on {desc.material} resonance")

    # texture
    if tech.texture:
        parts.append(f", {tech.texture} texture")
    if tech.density and tech.density != "moderate":
        parts.append(f", {tech.density}")

    # ── 2. soft constraints from mix context ──
    if mix_context:
        try:
            from oram.ears.mix_context import format_mix_constraints
            constraint_text = format_mix_constraints(mix_context)
            if constraint_text:
                parts.append(f". {constraint_text}")
        except Exception:
            pass

    # ── 3. creative seed ──
    if spec.impossible_room:
        parts.append(f", {spec.impossible_room}")
    elif desc.environment:
        parts.append(f", {desc.environment}")

    # ── 4. negative constraints ──
    parts.append(", no drums unless rhythmic source, no speech, no vocals")

    return " ".join(parts).replace("  ", " ").strip()


def compile_prompt(
    report: ListeningReport,
    engine: str,
    mix_context: MixContext | None = None,
) -> str:
    """compile engine-specific prompt from a listening report.

    engine: "sfx" | "voice" | "music"
    mix_context: optional multi-layer context for complementary generation.
    """
    compilers = {
        "sfx": compile_sfx_prompt,
        "voice": compile_sfx_prompt,  # ORAM never generates speech
        "music": compile_music_prompt,
    }
    compiler = compilers.get(engine, compile_sfx_prompt)
    return compiler(report, mix_context)
