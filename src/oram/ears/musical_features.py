"""oram.ears.musical_features — concrete musical data extraction.

the current listening system describes sounds with adjectives.
this module extracts measurable musical parameters:
pitch, tempo, harmonics, spectral shape, rhythmic pattern, key.

these become hard constraints for generation — tight input, open output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ── constants ──────────────────────────────────────────────────────────

_NOTE_NAMES: list[str] = [
    "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B",
]

SPECTRAL_BAND_NAMES: list[str] = [
    "sub-bass", "bass", "low-mid", "mid",
    "upper-mid", "presence", "brilliance", "air",
]

_SPECTRAL_BAND_EDGES: list[tuple[float, float]] = [
    (20, 100), (100, 300), (300, 800), (800, 2000),
    (2000, 4000), (4000, 8000), (8000, 14000), (14000, 20000),
]

# Krumhansl-Schmuckler key profiles (starting from C)
_KS_MAJOR: np.ndarray = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_KS_MINOR: np.ndarray = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


# ── helpers ────────────────────────────────────────────────────────────

def _to_mono(buffer: np.ndarray) -> np.ndarray:
    """convert buffer to mono float64 for processing."""
    if buffer.ndim > 1:
        mono = np.mean(buffer, axis=1)
    else:
        mono = buffer.copy()
    return mono.astype(np.float64)


def hz_to_note(hz: float) -> str:
    """convert a frequency in Hz to the nearest note name with octave.

    uses A4 = 440 Hz as reference.  returns names like 'A4', 'C#3', 'Eb5'.
    returns '' for hz <= 0.
    """
    if hz <= 0:
        return ""
    semitones_from_a4 = 12.0 * np.log2(hz / 440.0)
    midi_number = round(semitones_from_a4) + 69
    note_index = midi_number % 12
    octave = (midi_number // 12) - 1
    return f"{_NOTE_NAMES[note_index]}{octave}"


def _spectral_flux_envelope(
    mono: np.ndarray,
    sample_rate: int,
    frame_size: int = 1024,
    hop: int = 512,
) -> np.ndarray:
    """compute half-wave rectified spectral flux onset strength envelope.

    returns an array of flux values, one per frame.
    """
    length = len(mono)
    num_frames = max(1, (length - frame_size) // hop)
    if num_frames < 2:
        return np.array([], dtype=np.float64)

    prev_spectrum = None
    flux_values: list[float] = []

    for i in range(num_frames):
        start = i * hop
        frame = mono[start:start + frame_size]
        windowed = frame * np.hanning(frame_size)
        spectrum = np.abs(np.fft.rfft(windowed))

        if prev_spectrum is not None:
            diff = spectrum - prev_spectrum
            flux = float(np.sum(np.maximum(diff, 0.0)))
            flux_values.append(flux)

        prev_spectrum = spectrum

    return np.array(flux_values, dtype=np.float64)


def _detect_onsets(
    mono: np.ndarray,
    sample_rate: int,
    frame_size: int = 1024,
    hop: int = 512,
) -> np.ndarray:
    """detect onset times in seconds via spectral flux peak-picking."""
    flux = _spectral_flux_envelope(mono, sample_rate, frame_size, hop)
    if len(flux) == 0:
        return np.array([], dtype=np.float64)

    threshold = np.mean(flux) + 1.5 * np.std(flux)
    onset_frames = np.where(flux > threshold)[0]
    # each flux value corresponds to frame index (i+1) since we diff
    onset_times = (onset_frames + 1) * hop / sample_rate
    return onset_times


# ── pitch detection ───────────────────────────────────────────────────

def detect_pitch(
    mono: np.ndarray, sample_rate: int
) -> tuple[float, str, float]:
    """autocorrelation-based pitch detection.

    searches for the dominant fundamental between 50 Hz and 4000 Hz.
    returns (pitch_hz, pitch_note, confidence).
    """
    if len(mono) < 256:
        return (0.0, "", 0.0)

    # work on a centred, windowed signal
    sig = mono - np.mean(mono)
    sig = sig * np.hanning(len(sig))

    # normalized autocorrelation via FFT (Wiener-Khinchin)
    n = len(sig)
    fft_size = 1
    while fft_size < 2 * n:
        fft_size *= 2
    fft_sig = np.fft.rfft(sig, fft_size)
    acf = np.fft.irfft(fft_sig * np.conj(fft_sig))[:n]

    # normalize so lag-0 == 1.0
    if acf[0] < 1e-12:
        return (0.0, "", 0.0)
    acf = acf / acf[0]

    # search range in samples
    min_lag = max(1, int(sample_rate / 4000))  # 4000 Hz
    max_lag = min(n - 1, int(sample_rate / 50))  # 50 Hz

    if min_lag >= max_lag:
        return (0.0, "", 0.0)

    search = acf[min_lag:max_lag + 1]
    if len(search) == 0:
        return (0.0, "", 0.0)

    peak_idx = int(np.argmax(search))
    confidence = float(search[peak_idx])

    if confidence < 0.15:
        return (0.0, "", 0.0)

    lag = peak_idx + min_lag
    if lag == 0:
        return (0.0, "", 0.0)

    pitch_hz = float(sample_rate / lag)
    pitch_note = hz_to_note(pitch_hz)

    return (pitch_hz, pitch_note, confidence)


# ── BPM estimation ───────────────────────────────────────────────────

def estimate_bpm(
    mono: np.ndarray, sample_rate: int
) -> tuple[float, float]:
    """BPM estimation via onset autocorrelation.

    computes spectral flux onset envelope, then searches its
    autocorrelation for periodicity in the 40–220 BPM range.
    returns (bpm, confidence).
    """
    hop = 512
    flux = _spectral_flux_envelope(mono, sample_rate, frame_size=1024, hop=hop)
    if len(flux) < 4:
        return (0.0, 0.0)

    # normalize flux
    flux = flux - np.mean(flux)
    std = np.std(flux)
    if std < 1e-12:
        return (0.0, 0.0)
    flux = flux / std

    # autocorrelation of onset envelope
    n = len(flux)
    fft_size = 1
    while fft_size < 2 * n:
        fft_size *= 2
    fft_flux = np.fft.rfft(flux, fft_size)
    acf = np.fft.irfft(fft_flux * np.conj(fft_flux))[:n]

    if acf[0] < 1e-12:
        return (0.0, 0.0)
    acf = acf / acf[0]

    # onset envelope frame rate
    frame_rate = sample_rate / hop  # frames per second

    # BPM range → lag range in frames
    #   BPM = 60 * frame_rate / lag  →  lag = 60 * frame_rate / BPM
    min_lag = max(1, int(60.0 * frame_rate / 220.0))
    max_lag = min(n - 1, int(60.0 * frame_rate / 40.0))

    if min_lag >= max_lag:
        return (0.0, 0.0)

    search = acf[min_lag:max_lag + 1]
    if len(search) == 0:
        return (0.0, 0.0)

    peak_idx = int(np.argmax(search))
    confidence = float(search[peak_idx])

    if confidence < 0.05:
        return (0.0, 0.0)

    lag = peak_idx + min_lag
    if lag == 0:
        return (0.0, 0.0)

    bpm = float(60.0 * frame_rate / lag)
    return (bpm, min(confidence, 1.0))


# ── harmonic extraction ──────────────────────────────────────────────

def extract_harmonics(
    mono: np.ndarray, sample_rate: int, fundamental_hz: float
) -> list[float]:
    """extract harmonic ratios relative to the fundamental.

    finds peaks in the FFT near integer multiples of fundamental_hz
    (within ±5%).  returns at most 8 ratios.
    """
    if fundamental_hz <= 0 or len(mono) < 256:
        return []

    windowed = mono * np.hanning(len(mono))
    fft_data = np.fft.rfft(windowed)
    magnitudes = np.abs(fft_data)
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)

    # noise floor — ignore bins below this
    noise_floor = np.mean(magnitudes) * 0.5
    nyquist = sample_rate / 2.0

    ratios: list[float] = []
    for harmonic_num in range(1, 17):  # search up to 16th partial
        target_hz = fundamental_hz * harmonic_num
        if target_hz > nyquist:
            break

        # ±5% search window
        lo = target_hz * 0.95
        hi = target_hz * 1.05
        mask = (freqs >= lo) & (freqs <= hi)

        if not np.any(mask):
            continue

        region = magnitudes[mask]
        peak_mag = float(np.max(region))

        if peak_mag > noise_floor:
            # find exact peak frequency
            region_freqs = freqs[mask]
            peak_freq = float(region_freqs[np.argmax(region)])
            ratio = peak_freq / fundamental_hz
            ratios.append(round(ratio, 2))

        if len(ratios) >= 8:
            break

    return ratios


# ── spectral envelope ────────────────────────────────────────────────

def spectral_envelope(
    mono: np.ndarray, sample_rate: int
) -> list[float]:
    """compute an 8-band spectral energy fingerprint.

    divides the spectrum into 8 perceptual bands and returns
    the relative energy in each.  values sum to ~1.0.
    """
    if len(mono) < 256:
        return [0.125] * 8

    windowed = mono * np.hanning(len(mono))
    fft_data = np.fft.rfft(windowed)
    power = np.abs(fft_data) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)

    band_energies: list[float] = []
    for lo, hi in _SPECTRAL_BAND_EDGES:
        mask = (freqs >= lo) & (freqs < hi)
        band_energies.append(float(np.sum(power[mask])))

    total = sum(band_energies)
    if total < 1e-20:
        return [0.125] * 8

    return [e / total for e in band_energies]


# ── onset pattern ────────────────────────────────────────────────────

def onset_pattern(
    mono: np.ndarray, sample_rate: int, grid_steps: int = 16
) -> str:
    """quantize detected onsets to a rhythmic grid.

    divides the buffer into grid_steps equal time slots and marks
    each slot with 'x' (onset present) or '.' (silent).
    """
    if grid_steps < 1:
        return ""

    duration = len(mono) / sample_rate
    if duration < 0.01:
        return "." * grid_steps

    onset_times = _detect_onsets(mono, sample_rate)

    if len(onset_times) == 0:
        return "." * grid_steps

    slot_duration = duration / grid_steps
    grid: list[str] = []

    for step in range(grid_steps):
        slot_start = step * slot_duration
        slot_end = (step + 1) * slot_duration
        # check if any onset falls in this slot
        hit = np.any((onset_times >= slot_start) & (onset_times < slot_end))
        grid.append("x" if hit else ".")

    return "".join(grid)


# ── key estimation ───────────────────────────────────────────────────

def estimate_key(
    mono: np.ndarray, sample_rate: int
) -> tuple[str, float]:
    """key estimation via chroma vector + Krumhansl-Schmuckler profiles.

    computes a 12-bin chroma vector from the FFT and correlates it
    against all 24 major/minor key profiles.
    returns (key_name, confidence) like ('C major', 0.87).
    """
    if len(mono) < 256:
        return ("", 0.0)

    # compute chroma vector
    windowed = mono * np.hanning(len(mono))
    fft_data = np.fft.rfft(windowed)
    magnitudes = np.abs(fft_data)
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)

    chroma = np.zeros(12, dtype=np.float64)

    # accumulate energy into chroma bins
    # only use frequencies between ~32 Hz (C1) and ~4200 Hz (C8)
    valid = (freqs >= 32.0) & (freqs <= 4200.0)
    valid_freqs = freqs[valid]
    valid_mags = magnitudes[valid]

    if len(valid_freqs) == 0 or np.sum(valid_mags) < 1e-12:
        return ("", 0.0)

    # map each frequency bin to its nearest chroma class
    # semitone = 12 * log2(f / C0), C0 ≈ 16.35 Hz
    semitones = 12.0 * np.log2(valid_freqs / 16.3516)
    chroma_indices = np.round(semitones).astype(int) % 12

    for i in range(12):
        mask = chroma_indices == i
        chroma[i] = float(np.sum(valid_mags[mask] ** 2))

    total_chroma = np.sum(chroma)
    if total_chroma < 1e-20:
        return ("", 0.0)
    chroma = chroma / total_chroma

    # correlate with all 24 key profiles
    best_corr = -2.0
    best_key = ""

    for shift in range(12):
        rotated_major = np.roll(_KS_MAJOR, shift)
        rotated_minor = np.roll(_KS_MINOR, shift)

        corr_major = float(np.corrcoef(chroma, rotated_major)[0, 1])
        corr_minor = float(np.corrcoef(chroma, rotated_minor)[0, 1])

        root_name = _NOTE_NAMES[shift]

        if corr_major > best_corr:
            best_corr = corr_major
            best_key = f"{root_name} major"
        if corr_minor > best_corr:
            best_corr = corr_minor
            best_key = f"{root_name} minor"

    # map correlation (-1..1) → confidence (0..1)
    confidence = float(np.clip((best_corr + 1.0) / 2.0, 0.0, 1.0))

    return (best_key, confidence)


# ── combined extraction ──────────────────────────────────────────────

@dataclass
class MusicalFeatures:
    """all measurable musical parameters for an audio buffer."""

    pitch_hz: float = 0.0
    pitch_note: str = ""
    pitch_confidence: float = 0.0
    bpm: float = 0.0
    bpm_confidence: float = 0.0
    harmonic_ratios: list[float] = field(default_factory=list)
    spectral_shape: list[float] = field(default_factory=list)
    onset_grid: str = ""
    key_estimate: str = ""
    key_confidence: float = 0.0


def extract_musical_features(
    buffer: np.ndarray, sample_rate: int
) -> MusicalFeatures:
    """extract all musical features from an audio buffer."""
    if buffer.size == 0:
        return MusicalFeatures()

    mono = _to_mono(buffer)

    features = MusicalFeatures()

    # pitch
    features.pitch_hz, features.pitch_note, features.pitch_confidence = (
        detect_pitch(mono, sample_rate)
    )

    # tempo
    features.bpm, features.bpm_confidence = estimate_bpm(mono, sample_rate)

    # harmonics (only if pitch was found)
    features.harmonic_ratios = extract_harmonics(
        mono, sample_rate, features.pitch_hz
    )

    # spectral shape
    features.spectral_shape = spectral_envelope(mono, sample_rate)

    # onset grid
    features.onset_grid = onset_pattern(mono, sample_rate)

    # key
    features.key_estimate, features.key_confidence = estimate_key(
        mono, sample_rate
    )

    return features
