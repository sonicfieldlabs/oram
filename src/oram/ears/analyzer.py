"""oram.ears.analyzer — local audio analysis for listening reports.

analyzes:
- RMS level, peak level
- rough spectral centroid
- low/mid/high energy balance
- onset density
- dynamic range
- silence ratio
- repetition/fatigue heuristic
- speech residue placeholder
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from oram.types import OramSession


@dataclass
class AnalysisResult:
    """result of analyzing a single audio buffer."""

    rms: float = 0.0
    peak: float = 0.0
    spectral_centroid_hz: float = 0.0
    low_energy: float = 0.0   # 20-300 Hz
    mid_energy: float = 0.0   # 300-4000 Hz
    high_energy: float = 0.0  # 4000-20000 Hz
    onset_density: float = 0.0  # onsets per second
    dynamic_range_db: float = 0.0
    silence_ratio: float = 0.0
    fatigue_seconds: float | None = None
    speech_residue: bool = False
    # v2: improved features
    spectral_flatness: float = 0.0    # 0=tonal, 1=white noise
    zero_crossing_rate: float = 0.0   # crossings per second
    spectral_rolloff_hz: float = 0.0  # freq below which 85% energy sits
    crest_factor: float = 0.0         # peak / rms — transient sharpness


@dataclass
class LayerAnalysis:
    """analysis paired with layer metadata."""

    layer_id: int
    layer_name: str
    duration_seconds: float
    effects: list[str] = field(default_factory=list)
    is_generated: bool = False
    muted: bool = False
    analysis: AnalysisResult = field(default_factory=AnalysisResult)


@dataclass
class ListeningReport:
    """a complete listening report for a session."""

    session_id: str
    scene: str
    observations: list[str] = field(default_factory=list)
    layer_analyses: list[LayerAnalysis] = field(default_factory=list)

def _windowed_spectral_centroid(mono: np.ndarray, sample_rate: int,
                                 window_size: int = 4096,
                                 hop: int = 2048) -> float:
    """compute spectral centroid averaged over overlapping windows."""
    centroids = []
    length = len(mono)
    for start in range(0, length - window_size + 1, hop):
        frame = mono[start:start + window_size]
        # apply hann window to reduce spectral leakage
        windowed = frame * np.hanning(window_size)
        fft = np.fft.rfft(windowed)
        magnitudes = np.abs(fft)
        freqs = np.fft.rfftfreq(window_size, 1.0 / sample_rate)
        total_mag = np.sum(magnitudes)
        if total_mag > 1e-10:
            centroids.append(float(np.sum(freqs * magnitudes) / total_mag))
    if not centroids:
        return 0.0
    return float(np.mean(centroids))


def _spectral_flatness(mono: np.ndarray, window_size: int = 4096,
                        hop: int = 2048) -> float:
    """compute spectral flatness (Wiener entropy).

    ratio of geometric mean to arithmetic mean of the power spectrum.
    0 = purely tonal, 1 = white noise.
    """
    flatness_values = []
    length = len(mono)
    for start in range(0, length - window_size + 1, hop):
        frame = mono[start:start + window_size]
        windowed = frame * np.hanning(window_size)
        power = np.abs(np.fft.rfft(windowed)) ** 2
        # skip DC and near-zero bins
        power = power[1:]
        power = np.maximum(power, 1e-20)  # prevent log(0)
        geo_mean = np.exp(np.mean(np.log(power)))
        arith_mean = np.mean(power)
        if arith_mean > 1e-20:
            flatness_values.append(float(geo_mean / arith_mean))
    if not flatness_values:
        return 0.0
    return float(np.clip(np.mean(flatness_values), 0.0, 1.0))


def _zero_crossing_rate(mono: np.ndarray, sample_rate: int) -> float:
    """compute zero-crossing rate (crossings per second)."""
    if len(mono) < 2:
        return 0.0
    signs = np.sign(mono)
    # count sign changes
    crossings = int(np.sum(np.abs(np.diff(signs)) > 0))
    duration = len(mono) / sample_rate
    return crossings / max(duration, 0.001)


def _spectral_rolloff(mono: np.ndarray, sample_rate: int,
                       threshold: float = 0.85) -> float:
    """compute spectral rolloff — freq below which threshold% of energy sits."""
    if len(mono) < 256:
        return 0.0
    fft = np.fft.rfft(mono)
    power = np.abs(fft) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)
    cumulative = np.cumsum(power)
    total = cumulative[-1]
    if total < 1e-20:
        return 0.0
    idx = np.searchsorted(cumulative, threshold * total)
    idx = min(idx, len(freqs) - 1)
    return float(freqs[idx])


def _spectral_flux_onsets(mono: np.ndarray, sample_rate: int,
                           frame_size: int = 1024,
                           hop: int = 512) -> float:
    """count onsets using spectral flux — more accurate than energy diff.

    spectral flux measures the change in spectral shape between frames,
    which is more robust to noise than raw energy differences.
    """
    length = len(mono)
    num_frames = max(1, (length - frame_size) // hop)
    if num_frames < 2:
        return 0.0

    prev_spectrum = None
    flux_values = []
    for i in range(num_frames):
        start = i * hop
        frame = mono[start:start + frame_size]
        windowed = frame * np.hanning(frame_size)
        spectrum = np.abs(np.fft.rfft(windowed))

        if prev_spectrum is not None:
            # half-wave rectified spectral flux (only increases count)
            diff = spectrum - prev_spectrum
            flux = float(np.sum(np.maximum(diff, 0.0)))
            flux_values.append(flux)

        prev_spectrum = spectrum

    if not flux_values:
        return 0.0

    flux_arr = np.array(flux_values)
    # adaptive threshold: mean + 1.5 * std
    threshold = np.mean(flux_arr) + 1.5 * np.std(flux_arr)
    onsets = int(np.sum(flux_arr > threshold))
    duration = length / sample_rate
    return float(onsets / max(duration, 0.01))


def analyze_buffer(buffer: np.ndarray, sample_rate: int = 48000) -> AnalysisResult:
    """analyze a single audio buffer."""
    if buffer.shape[0] == 0:
        return AnalysisResult()

    # mono-ize
    if buffer.ndim > 1:
        mono = np.mean(buffer, axis=1)
    else:
        mono = buffer

    result = AnalysisResult()

    # RMS and peak
    result.rms = float(np.sqrt(np.mean(mono**2)))
    result.peak = float(np.max(np.abs(mono)))

    # crest factor (peak / rms) — indicates transient sharpness
    if result.rms > 1e-10:
        result.crest_factor = float(result.peak / result.rms)
    else:
        result.crest_factor = 0.0

    # spectral centroid — windowed average over the full buffer
    if len(mono) > 256:
        result.spectral_centroid_hz = _windowed_spectral_centroid(mono, sample_rate)

    # energy bands
    if len(mono) > 256:
        fft_full = np.fft.rfft(mono)
        freqs_full = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)
        mags = np.abs(fft_full)

        low_mask = (freqs_full >= 20) & (freqs_full < 300)
        mid_mask = (freqs_full >= 300) & (freqs_full < 4000)
        high_mask = (freqs_full >= 4000) & (freqs_full < 20000)

        total = np.sum(mags) + 1e-10
        result.low_energy = float(np.sum(mags[low_mask]) / total)
        result.mid_energy = float(np.sum(mags[mid_mask]) / total)
        result.high_energy = float(np.sum(mags[high_mask]) / total)

    # spectral flatness — tonal vs noisy
    if len(mono) > 256:
        result.spectral_flatness = _spectral_flatness(mono)

    # zero-crossing rate
    result.zero_crossing_rate = _zero_crossing_rate(mono, sample_rate)

    # spectral rolloff
    if len(mono) > 256:
        result.spectral_rolloff_hz = _spectral_rolloff(mono, sample_rate)

    # onset density — spectral flux (replaces energy-diff method)
    if len(mono) > 1024:
        result.onset_density = _spectral_flux_onsets(mono, sample_rate)

    # dynamic range
    if result.peak > 0 and result.rms > 0:
        result.dynamic_range_db = float(20 * np.log10(result.peak / max(result.rms, 1e-10)))

    # silence ratio
    threshold = 0.01
    silent_samples = np.sum(np.abs(mono) < threshold)
    result.silence_ratio = float(silent_samples / len(mono))

    # repetition fatigue heuristic
    if len(mono) > sample_rate * 2:
        # compare first and second halves
        half = len(mono) // 2
        corr = np.corrcoef(
            mono[:min(half, sample_rate * 4)],
            mono[half:half + min(half, sample_rate * 4)]
        )[0, 1]
        if abs(corr) > 0.7:
            result.fatigue_seconds = float(len(mono) / sample_rate * 0.8)

    return result


def analyze_session(session: OramSession) -> ListeningReport:
    """generate a listening report for the entire session."""
    report = ListeningReport(session_id=session.id, scene=session.scene)

    for layer in session.layers:
        if layer.is_empty:
            continue

        analysis = analyze_buffer(layer.buffer, session.sample_rate)
        layer_analysis = LayerAnalysis(
            layer_id=layer.slot + 1,
            layer_name=layer.name,
            duration_seconds=layer.duration_seconds,
            effects=layer.effects_applied.copy(),
            is_generated=layer.is_generated,
            muted=layer.muted,
            analysis=analysis,
        )
        report.layer_analyses.append(layer_analysis)

    # generate observations
    report.observations = _generate_observations(report)
    return report


def _generate_observations(report: ListeningReport) -> list[str]:
    """generate textual observations from analysis data."""
    obs = []

    if not report.layer_analyses:
        obs.append("empty session — no active layers")
        return obs

    # overall density
    avg_rms = np.mean([la.analysis.rms for la in report.layer_analyses])
    if avg_rms > 0.3:
        obs.append("dense overall mix")
    elif avg_rms < 0.05:
        obs.append("very sparse, near-silent mix")

    # spectral balance
    avg_centroid = np.mean([la.analysis.spectral_centroid_hz for la in report.layer_analyses])
    if avg_centroid > 4000:
        obs.append("bright, high-frequency dominated")
    elif avg_centroid < 800:
        obs.append("dark, low-frequency dominated")
    else:
        obs.append("balanced mid-frequency content")

    # spatial depth
    reverbed = any("reverb" in e or "spatial_far" in e
                    for la in report.layer_analyses for e in la.effects)
    if reverbed:
        obs.append("spatial depth present")
    else:
        obs.append("low spatial depth")

    # generated content
    generated = [la for la in report.layer_analyses if la.is_generated]
    if generated:
        obs.append("synthetic/generated texture present")

    # fatigue
    for la in report.layer_analyses:
        if la.analysis.fatigue_seconds:
            obs.append(
                f"repetition fatigue likely after {la.analysis.fatigue_seconds:.0f} seconds"
            )
            break

    # silence
    for la in report.layer_analyses:
        if la.analysis.silence_ratio > 0.5:
            obs.append(f"high silence ratio in {la.layer_name}")

    return obs
