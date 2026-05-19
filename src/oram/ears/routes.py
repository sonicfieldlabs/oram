"""oram.ears.routes — formalized listening routes.

four modes of listening, each producing structured reports:
- technical: audio engineer analysis
- descriptive: sound designer inference
- speculative: poetic/conceptual interpretation
- hybrid: all three combined + generation instruction
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from oram.ears.analyzer import AnalysisResult, analyze_buffer


@dataclass
class TechnicalReport:
    """what an audio engineer hears."""

    duration: float = 0.0
    loudness_rms: float = 0.0
    peak: float = 0.0
    spectral_centroid_hz: float = 0.0
    noise_balance: str = ""  # noisy / clean / mixed
    transient_type: str = ""  # sharp / soft / irregular
    pitch_tendency: str = ""  # tonal / atonal / ambiguous
    rhythm: str = ""  # regular / irregular / none
    attack_profile: str = ""  # fast / slow / gradual
    decay_profile: str = ""  # long / short / sustained
    texture: str = ""  # grainy / smooth / rough / brittle
    density: str = ""  # dense / sparse / moderate
    dynamic_range: str = ""  # wide / narrow / compressed
    possible_source: str = ""
    recording_quality: str = ""  # close / distant / room / outdoor

    # raw numeric for engine router
    pitch_confidence: float = 0.0
    rhythmic_regularity: float = 0.0
    is_noisy: bool = False
    is_gestural: bool = False
    contains_speech: bool = False
    contains_voice: bool = False


@dataclass
class DescriptiveReport:
    """what a sound designer hears."""

    resembles: str = ""
    action: str = ""
    environment: str = ""
    material: str = ""
    cinematic_context: str = ""


@dataclass
class SpeculativeReport:
    """what a poet hears."""

    imaginary_thing: str = ""
    hidden_body: str = ""
    impossible_room: str = ""
    non_human_gesture: str = ""
    sonic_fiction: str = ""


@dataclass
class ListeningReport:
    """complete listening report from any route."""

    route: str = "hybrid"
    technical: TechnicalReport = field(default_factory=TechnicalReport)
    descriptive: DescriptiveReport = field(default_factory=DescriptiveReport)
    speculative: SpeculativeReport = field(default_factory=SpeculativeReport)
    generation_instruction: str = ""
    raw_analysis: AnalysisResult | None = None


class ListeningRouteProtocol(Protocol):
    """protocol for listening routes."""

    def listen(self, buffer: np.ndarray, sample_rate: int) -> ListeningReport: ...


class TechnicalRoute:
    """listens like an audio engineer. local analysis, no LLM needed."""

    def listen(self, buffer: np.ndarray, sample_rate: int) -> ListeningReport:
        analysis = analyze_buffer(buffer, sample_rate)
        duration = len(buffer) / sample_rate

        tech = TechnicalReport(
            duration=round(duration, 2),
            loudness_rms=round(analysis.rms, 4),
            peak=round(analysis.peak, 4),
            spectral_centroid_hz=round(analysis.spectral_centroid_hz, 1),
        )

        # ── noise balance — spectral flatness is the gold standard ──
        sf = analysis.spectral_flatness
        if sf > 0.4:
            tech.noise_balance = "noisy"
            tech.is_noisy = True
        elif sf > 0.15:
            tech.noise_balance = "mixed"
            tech.is_noisy = False
        elif sf > 0.05:
            tech.noise_balance = "mostly clean"
        else:
            tech.noise_balance = "clean"

        # ── pitch / tonal — spectral flatness + band energy ──
        if sf < 0.08:
            tech.pitch_tendency = "strongly tonal"
            tech.pitch_confidence = 0.9
        elif sf < 0.15:
            tech.pitch_tendency = "tonal"
            tech.pitch_confidence = 0.7
        elif sf < 0.3:
            tech.pitch_tendency = "ambiguous"
            tech.pitch_confidence = 0.4
        else:
            tech.pitch_tendency = "atonal / noise-like"
            tech.pitch_confidence = 0.1

        # refine with band energy for low tonal content
        if tech.pitch_confidence < 0.5 and analysis.low_energy > 0.5:
            tech.pitch_tendency = "low-frequency tonal"
            tech.pitch_confidence = 0.6

        # ── transient / attack — crest factor + onset density ──
        crest = analysis.crest_factor
        if crest > 10:
            tech.transient_type = "sharp impulsive"
            tech.attack_profile = "very fast"
            tech.is_gestural = True
        elif crest > 5:
            tech.transient_type = "sharp"
            tech.attack_profile = "fast"
            tech.is_gestural = True
        elif crest > 3:
            tech.transient_type = "moderate"
            tech.attack_profile = "gradual"
            tech.is_gestural = analysis.onset_density > 3
        elif crest > 1.5:
            tech.transient_type = "soft"
            tech.attack_profile = "slow"
        else:
            tech.transient_type = "flat / sustained"
            tech.attack_profile = "none"

        # ── rhythm — onset density ──
        if analysis.onset_density > 8:
            tech.rhythm = "dense rhythmic"
            tech.rhythmic_regularity = 0.8
        elif analysis.onset_density > 4:
            tech.rhythm = "regular"
            tech.rhythmic_regularity = 0.6
        elif analysis.onset_density > 2:
            tech.rhythm = "irregular"
            tech.rhythmic_regularity = 0.3
        elif analysis.onset_density > 0.5:
            tech.rhythm = "sparse"
            tech.rhythmic_regularity = 0.1
        else:
            tech.rhythm = "none"
            tech.rhythmic_regularity = 0.0

        # ── texture — ZCR + spectral flatness + dynamic range ──
        zcr = analysis.zero_crossing_rate
        dr = analysis.dynamic_range_db
        centroid = analysis.spectral_centroid_hz
        rolloff = analysis.spectral_rolloff_hz

        # primary texture from spectral flatness and ZCR
        if sf > 0.4 and zcr > 8000:
            tech.texture = "hissy / airy"
        elif sf > 0.3 and zcr > 5000:
            tech.texture = "breathy / diffuse"
        elif sf > 0.3:
            tech.texture = "grainy"
        elif sf < 0.08 and dr < 6:
            tech.texture = "smooth / sustained"
        elif sf < 0.08 and centroid > 3000:
            tech.texture = "bright / metallic"
        elif sf < 0.08 and centroid < 500:
            tech.texture = "dark / warm"
        elif sf < 0.15 and dr > 15:
            tech.texture = "brittle / cracking"
        elif sf < 0.15:
            tech.texture = "resonant"
        elif dr > 20:
            tech.texture = "rough / fractured"
        elif dr > 12:
            tech.texture = "granular"
        else:
            tech.texture = "neutral"

        # ── density ──
        if analysis.rms > 0.2 and analysis.onset_density > 5:
            tech.density = "dense"
        elif analysis.rms > 0.1 or analysis.onset_density > 3:
            tech.density = "moderate"
        elif analysis.silence_ratio > 0.4:
            tech.density = "very sparse"
        else:
            tech.density = "sparse"

        # ── decay ──
        if analysis.silence_ratio > 0.4:
            tech.decay_profile = "short with silence"
        elif analysis.silence_ratio > 0.2:
            tech.decay_profile = "moderate decay"
        elif crest < 2:
            tech.decay_profile = "sustained / continuous"
        else:
            tech.decay_profile = "natural decay"

        # ── dynamic range ──
        if dr > 25:
            tech.dynamic_range = "very wide"
        elif dr > 18:
            tech.dynamic_range = "wide"
        elif dr > 10:
            tech.dynamic_range = "moderate"
        elif dr > 5:
            tech.dynamic_range = "narrow"
        else:
            tech.dynamic_range = "compressed / flat"

        # ── recording quality — spectral rolloff + centroid ──
        if rolloff > 12000 and analysis.rms > 0.1:
            tech.recording_quality = "close / intimate"
        elif rolloff > 8000:
            tech.recording_quality = "studio / treated room"
        elif rolloff > 4000:
            tech.recording_quality = "room / mid-distance"
        elif rolloff > 2000:
            tech.recording_quality = "distant / reverberant"
        else:
            tech.recording_quality = "very distant / muffled"

        # ── possible source inference from feature combinations ──
        if tech.contains_speech or (sf < 0.15 and 200 < centroid < 3000 and zcr < 3000):
            tech.possible_source = "voice or speech"
            tech.contains_voice = True
        elif centroid > 5000 and sf > 0.3:
            tech.possible_source = "friction / contact noise"
        elif centroid > 3000 and sf < 0.1:
            tech.possible_source = "metallic resonance"
        elif centroid < 300 and sf < 0.1:
            tech.possible_source = "low drone or engine"
        elif centroid < 500 and sf < 0.15:
            tech.possible_source = "bass tone or subharmonic"
        elif analysis.onset_density > 6 and crest > 5:
            tech.possible_source = "percussive / impact sequence"
        elif sf > 0.35 and zcr > 6000:
            tech.possible_source = "wind / breath / environmental noise"
        elif sf > 0.25 and analysis.onset_density < 1:
            tech.possible_source = "ambient noise floor"
        elif 500 < centroid < 2000 and sf < 0.2:
            tech.possible_source = "tonal instrument or resonant body"
        else:
            tech.possible_source = "unclassified sound material"

        report = ListeningReport(route="technical", technical=tech, raw_analysis=analysis)
        return report


class DescriptiveRoute:
    """listens like a sound designer. uses LLM for inference."""

    def __init__(self, llm_adapter=None):
        self._llm = llm_adapter

    def listen(self, buffer: np.ndarray, sample_rate: int) -> ListeningReport:
        # start with technical analysis
        tech_route = TechnicalRoute()
        report = tech_route.listen(buffer, sample_rate)
        report.route = "descriptive"

        if self._llm is None:
            # fallback: generate basic description from technical data
            report.descriptive = self._infer_from_technical(report.technical)
            return report

        # use LLM for richer description
        prompt = self._build_llm_prompt(report.technical)
        try:
            response = self._llm.complete(prompt)
            report.descriptive = self._parse_llm_response(response)
        except Exception:
            report.descriptive = self._infer_from_technical(report.technical)

        return report

    def _build_llm_prompt(self, tech: TechnicalReport) -> str:
        return (
            f"Describe this sound for sound design purposes. "
            f"Technical data: {tech.duration}s, {tech.texture} texture, "
            f"{tech.noise_balance} noise, {tech.transient_type} transients, "
            f"{tech.attack_profile} attack, {tech.recording_quality} mic distance, "
            f"spectral centroid {tech.spectral_centroid_hz:.0f}Hz. "
            f"Answer in this exact format:\n"
            f"resembles: [what it sounds like]\n"
            f"action: [what gesture/action produces it]\n"
            f"environment: [what space it implies]\n"
            f"material: [what object/surface/body]\n"
            f"context: [cinematic/game use]"
        )

    def _parse_llm_response(self, response) -> DescriptiveReport:
        text = str(response) if not isinstance(response, str) else response
        desc = DescriptiveReport()
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("resembles:"):
                desc.resembles = line.split(":", 1)[1].strip()
            elif line.startswith("action:"):
                desc.action = line.split(":", 1)[1].strip()
            elif line.startswith("environment:"):
                desc.environment = line.split(":", 1)[1].strip()
            elif line.startswith("material:"):
                desc.material = line.split(":", 1)[1].strip()
            elif line.startswith("context:"):
                desc.cinematic_context = line.split(":", 1)[1].strip()
        return desc

    def _infer_from_technical(self, tech: TechnicalReport) -> DescriptiveReport:
        """feature-driven description without LLM."""
        desc = DescriptiveReport()
        centroid = tech.spectral_centroid_hz

        # material — inferred from spectral centroid
        if centroid > 6000:
            desc.material = "thin metal, glass, or wire"
        elif centroid > 3000:
            desc.material = "metallic or crystalline surface"
        elif centroid > 1500:
            desc.material = "wood, skin, or dense plastic"
        elif centroid > 500:
            desc.material = "resonant body or hollow object"
        elif centroid > 200:
            desc.material = "heavy mass or membrane"
        else:
            desc.material = "low-frequency mass or engine"

        # action — from transient and onset data
        if tech.transient_type in ("sharp impulsive", "sharp"):
            desc.action = "striking, hitting, or tapping"
        elif tech.is_noisy and tech.is_gestural:
            desc.action = "scraping, dragging, or rubbing"
        elif tech.is_noisy:
            desc.action = "sustained friction or air turbulence"
        elif tech.rhythmic_regularity > 0.5:
            desc.action = "repetitive mechanical motion"
        elif tech.attack_profile in ("slow", "none"):
            desc.action = "sustained resonance or continuous excitation"
        else:
            desc.action = "intermittent gesture"

        # environment — from recording quality
        if "close" in tech.recording_quality:
            desc.environment = "intimate, close-miked space"
        elif "studio" in tech.recording_quality:
            desc.environment = "controlled indoor environment"
        elif "room" in tech.recording_quality:
            desc.environment = "medium-sized room"
        elif "distant" in tech.recording_quality:
            desc.environment = "open or reverberant space"
        elif "muffled" in tech.recording_quality:
            desc.environment = "occluded or underwater"
        else:
            desc.environment = "unspecified space"

        # resembles — combine source + texture
        resembles_parts = []
        if tech.possible_source and "unclassified" not in tech.possible_source:
            resembles_parts.append(tech.possible_source)
        if tech.texture:
            resembles_parts.append(f"{tech.texture} character")
        if tech.density and tech.density != "moderate":
            resembles_parts.append(f"{tech.density} arrangement")
        desc.resembles = ", ".join(resembles_parts) if resembles_parts else f"{tech.texture or 'neutral'} material sound"

        # cinematic context
        if tech.is_noisy and tech.density == "dense":
            desc.cinematic_context = "tension, industrial, or horror scene"
        elif "tonal" in tech.pitch_tendency and tech.density == "sparse":
            desc.cinematic_context = "ambient underscore or atmospheric bed"
        elif tech.rhythmic_regularity > 0.5:
            desc.cinematic_context = "mechanical loop or rhythmic transition"
        elif tech.is_noisy and tech.density == "sparse":
            desc.cinematic_context = "environmental ambience or foley texture"
        else:
            desc.cinematic_context = "textural layer or sound design element"

        return desc


class SpeculativeRoute:
    """listens poetically and conceptually. uses LLM for interpretation."""

    def __init__(self, llm_adapter=None):
        self._llm = llm_adapter

    def listen(self, buffer: np.ndarray, sample_rate: int) -> ListeningReport:
        tech_route = TechnicalRoute()
        report = tech_route.listen(buffer, sample_rate)
        report.route = "speculative"

        if self._llm is None:
            report.speculative = self._infer_from_technical(report.technical)
            return report

        prompt = self._build_llm_prompt(report.technical)
        try:
            response = self._llm.complete(prompt)
            report.speculative = self._parse_llm_response(response)
        except Exception:
            report.speculative = self._infer_from_technical(report.technical)

        return report

    def _build_llm_prompt(self, tech: TechnicalReport) -> str:
        return (
            f"Listen to this sound speculatively. It is {tech.duration}s long, "
            f"{tech.texture} texture, {tech.noise_balance} noise character, "
            f"{tech.transient_type} transients, {tech.attack_profile} attack, "
            f"{tech.recording_quality} distance. "
            f"Answer poetically in this format:\n"
            f"imaginary_thing: [what impossible thing could this become]\n"
            f"hidden_body: [what hidden body is inside it]\n"
            f"impossible_room: [what non-existent room does it imply]\n"
            f"non_human_gesture: [what non-human gesture does it suggest]\n"
            f"sonic_fiction: [one sentence sonic fiction]"
        )

    def _parse_llm_response(self, response) -> SpeculativeReport:
        text = str(response) if not isinstance(response, str) else response
        spec = SpeculativeReport()
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("imaginary_thing:"):
                spec.imaginary_thing = line.split(":", 1)[1].strip()
            elif line.startswith("hidden_body:"):
                spec.hidden_body = line.split(":", 1)[1].strip()
            elif line.startswith("impossible_room:"):
                spec.impossible_room = line.split(":", 1)[1].strip()
            elif line.startswith("non_human_gesture:"):
                spec.non_human_gesture = line.split(":", 1)[1].strip()
            elif line.startswith("sonic_fiction:"):
                spec.sonic_fiction = line.split(":", 1)[1].strip()
        return spec

    def _infer_from_technical(self, tech: TechnicalReport) -> SpeculativeReport:
        """feature-driven speculative inference without LLM."""
        spec = SpeculativeReport()
        centroid = tech.spectral_centroid_hz

        # imaginary thing — shaped by spectral character
        if tech.is_noisy and centroid > 5000:
            spec.imaginary_thing = "radio static from a satellite that no longer exists"
        elif tech.is_noisy and centroid < 1000:
            spec.imaginary_thing = "the breathing of a buried machine"
        elif tech.is_noisy:
            spec.imaginary_thing = "wind passing through a structure made of rust"
        elif "metallic" in tech.texture:
            spec.imaginary_thing = "a bell cast from an impossible alloy"
        elif "warm" in tech.texture or "dark" in tech.texture:
            spec.imaginary_thing = "the resonance of a wooden chamber filled with warm fog"
        elif "smooth" in tech.texture or "sustained" in tech.texture:
            spec.imaginary_thing = "a glass thread stretched between two sleeping planets"
        elif "brittle" in tech.texture or "cracking" in tech.texture:
            spec.imaginary_thing = "frozen bone fracturing under thermal pressure"
        elif "airy" in tech.texture or "breathy" in tech.texture:
            spec.imaginary_thing = "the exhalation of an architecture"
        elif tech.rhythmic_regularity > 0.5:
            spec.imaginary_thing = "a clock inside a mineral"
        else:
            spec.imaginary_thing = "an object whose name was never invented"

        # hidden body — from possible source
        source = tech.possible_source or ""
        if "voice" in source:
            spec.hidden_body = "a throat lined with sand and silk"
        elif "metallic" in source:
            spec.hidden_body = "a thin sheet of copper vibrating inside a vacuum"
        elif "percussion" in source or "impact" in source:
            spec.hidden_body = "a dense sphere dropped into an acoustic void"
        elif "drone" in source or "engine" in source:
            spec.hidden_body = "an infinite cylinder rotating at the edge of hearing"
        elif "wind" in source or "breath" in source:
            spec.hidden_body = "a lung made of stone"
        elif "noise" in source or "ambient" in source:
            spec.hidden_body = "the collective hum of dormant electronics"
        elif "tonal" in source or "instrument" in source:
            spec.hidden_body = "a resonant cavity carved by erosion"
        else:
            spec.hidden_body = "something small, dense, and unnamed"

        # impossible room — from recording quality
        if "close" in tech.recording_quality:
            spec.impossible_room = "a room the size of a closed mouth"
        elif "distant" in tech.recording_quality or "reverberant" in tech.recording_quality:
            spec.impossible_room = "a cathedral built inside an iceberg"
        elif "muffled" in tech.recording_quality:
            spec.impossible_room = "a chamber beneath the ocean floor"
        else:
            spec.impossible_room = "a room that only exists when the sound plays"

        # non-human gesture — from transient character
        if tech.is_gestural and "sharp" in tech.transient_type:
            spec.non_human_gesture = "an insect striking a resonant surface"
        elif tech.is_gestural:
            spec.non_human_gesture = "a tectonic plate shifting by one millimeter"
        elif tech.is_noisy:
            spec.non_human_gesture = "electricity searching for ground"
        else:
            spec.non_human_gesture = "a mineral slowly changing phase"

        # sonic fiction
        if tech.is_noisy and tech.density == "dense":
            spec.sonic_fiction = "In the dead factory, the machines dream in static."
        elif "tonal" in tech.pitch_tendency:
            spec.sonic_fiction = "Somewhere deep, a frequency remembers what it was before it became sound."
        elif tech.density in ("sparse", "very sparse"):
            spec.sonic_fiction = "The silence between sounds is where the real performance lives."
        else:
            spec.sonic_fiction = "This sound has been traveling since before there were ears to hear it."

        return spec


class HybridRoute:
    """combines all three routes + generation instruction."""

    def __init__(self, llm_adapter=None):
        self._technical = TechnicalRoute()
        self._descriptive = DescriptiveRoute(llm_adapter)
        self._speculative = SpeculativeRoute(llm_adapter)

    def listen(self, buffer: np.ndarray, sample_rate: int) -> ListeningReport:
        # single technical pass — shared by all routes
        tech_report = self._technical.listen(buffer, sample_rate)

        # descriptive + speculative use the cached technical data
        desc_report = ListeningReport(route="descriptive", technical=tech_report.technical)
        if self._descriptive._llm:
            prompt = self._descriptive._build_llm_prompt(tech_report.technical)
            try:
                response = self._descriptive._llm.complete(prompt)
                desc_report.descriptive = self._descriptive._parse_llm_response(response)
            except Exception:
                desc_report.descriptive = self._descriptive._infer_from_technical(tech_report.technical)
        else:
            desc_report.descriptive = self._descriptive._infer_from_technical(tech_report.technical)

        spec_report = ListeningReport(route="speculative", technical=tech_report.technical)
        if self._speculative._llm:
            prompt = self._speculative._build_llm_prompt(tech_report.technical)
            try:
                response = self._speculative._llm.complete(prompt)
                spec_report.speculative = self._speculative._parse_llm_response(response)
            except Exception:
                spec_report.speculative = self._speculative._infer_from_technical(tech_report.technical)
        else:
            spec_report.speculative = self._speculative._infer_from_technical(tech_report.technical)

        report = ListeningReport(
            route="hybrid",
            technical=tech_report.technical,
            descriptive=desc_report.descriptive,
            speculative=spec_report.speculative,
            raw_analysis=tech_report.raw_analysis,
        )

        # generate a combined instruction
        report.generation_instruction = self._compile_instruction(report)
        return report

    def _compile_instruction(self, report: ListeningReport) -> str:
        """create a generation instruction from all three listening modes."""
        parts = []
        tech = report.technical
        desc = report.descriptive
        spec = report.speculative

        if desc.resembles:
            parts.append(desc.resembles)
        if tech.texture:
            parts.append(f"{tech.texture} texture")
        if tech.recording_quality:
            parts.append(f"{tech.recording_quality} distance")
        if spec.imaginary_thing:
            parts.append(spec.imaginary_thing)
        if tech.duration:
            parts.append(f"{tech.duration}s")

        return ", ".join(parts) if parts else "uncharacterized sound material"


def create_route(route_name: str, llm_adapter=None) -> ListeningRouteProtocol:
    """factory function for creating listening routes."""
    routes = {
        "technical": TechnicalRoute,
        "descriptive": lambda: DescriptiveRoute(llm_adapter),
        "speculative": lambda: SpeculativeRoute(llm_adapter),
        "hybrid": lambda: HybridRoute(llm_adapter),
    }
    factory = routes.get(route_name)
    if factory is None:
        raise ValueError(f"unknown listening route: {route_name}")
    return factory() if callable(factory) else factory
