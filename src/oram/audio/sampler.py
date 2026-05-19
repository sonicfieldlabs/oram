"""oram.audio.sampler — per-layer chromatic sampler behavior.

turns any layer's buffer into a playable instrument. supports:
- keyboard/MIDI note input
- pitch-shifted playback from root note
- ADSR envelope per voice
- configurable polyphony (default 4 voices)
- one-shot and gate modes
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from oram.types import ADSREnvelope, Layer, SamplerParams


@dataclass
class SamplerVoice:
    """a single active voice in the sampler."""

    note: int = 60
    velocity: float = 1.0
    phase: float = 0.0
    envelope_phase: str = "attack"  # attack / decay / sustain / release / off
    envelope_level: float = 0.0
    active: bool = True

    # pitch ratio relative to root note
    pitch_ratio: float = 1.0


class SamplerBehavior:
    """manages chromatic playback of a layer's buffer."""

    def __init__(self, layer: Layer):
        self.layer = layer
        self.voices: list[SamplerVoice] = []

    @property
    def params(self) -> SamplerParams:
        return self.layer.sampler

    @property
    def adsr(self) -> ADSREnvelope:
        return self.params.adsr

    def note_on(self, note: int, velocity: float = 1.0) -> None:
        """trigger a note. creates a new voice or steals the oldest."""
        if self.layer.is_empty:
            return

        # calculate pitch ratio (semitone distance from root)
        semitones = note - self.params.root_note + self.params.transpose
        pitch_ratio = 2.0 ** (semitones / 12.0) * (2.0 ** (self.params.fine_tune / 1200.0))

        # check polyphony
        active_voices = [v for v in self.voices if v.active]
        if len(active_voices) >= self.params.polyphony:
            # steal oldest voice
            oldest = active_voices[0]
            oldest.active = False
            self.voices.remove(oldest)

        voice = SamplerVoice(
            note=note,
            velocity=velocity if self.params.velocity_sensitivity else 1.0,
            phase=float(self.params.start_point),
            envelope_phase="attack",
            envelope_level=0.0,
            pitch_ratio=pitch_ratio,
        )
        self.voices.append(voice)

    def note_off(self, note: int) -> None:
        """release a note. triggers release phase of matching voices."""
        for voice in self.voices:
            if voice.note == note and voice.active:
                if self.params.mode == "one_shot":
                    # one-shot: ignore note_off
                    pass
                else:
                    voice.envelope_phase = "release"

    def get_next_block(self, block_size: int) -> np.ndarray:
        """render all active voices and return mixed output."""
        if not self.voices or self.layer.is_empty:
            return np.zeros((block_size, 2), dtype=np.float32)

        buf = self.layer.buffer
        end_point = self.params.end_point
        if end_point <= 0:
            end_point = buf.shape[0]

        output = np.zeros((block_size, 2), dtype=np.float32)
        sr = self.layer.sample_rate
        voices_to_remove = []

        for voice in self.voices:
            if not voice.active:
                voices_to_remove.append(voice)
                continue

            for i in range(block_size):
                # advance envelope
                env = self._process_envelope(voice, sr)
                if not voice.active:
                    break

                # read sample with linear interpolation
                pos = voice.phase
                idx = int(pos)
                frac = pos - idx

                if self.params.reverse:
                    idx = end_point - 1 - (idx % (end_point - self.params.start_point))
                else:
                    idx = self.params.start_point + (idx % (end_point - self.params.start_point))

                if 0 <= idx < buf.shape[0]:
                    # linear interpolation with clamped next index (§2.1)
                    idx_next = min(idx + 1, buf.shape[0] - 1)
                    sample = buf[idx] * (1 - frac) + buf[idx_next] * frac
                    sample *= env * voice.velocity
                    output[i] += sample

                # advance phase
                voice.phase += voice.pitch_ratio

                # check if we've reached the end
                if voice.phase >= (end_point - self.params.start_point):
                    if self.params.mode == "loop":
                        voice.phase = 0.0
                    else:
                        voice.active = False
                        break

        # clean up finished voices
        for v in voices_to_remove:
            if v in self.voices:
                self.voices.remove(v)
        self.voices = [v for v in self.voices if v.active]

        # clip
        peak = np.max(np.abs(output))
        if peak > 0.95:
            output *= 0.9 / peak

        return output

    def _process_envelope(self, voice: SamplerVoice, sr: int) -> float:
        """advance envelope and return current level."""
        adsr = self.adsr

        if voice.envelope_phase == "attack":
            attack_samples = max(1, int(adsr.attack * sr))
            voice.envelope_level += 1.0 / attack_samples
            if voice.envelope_level >= 1.0:
                voice.envelope_level = 1.0
                voice.envelope_phase = "decay"

        elif voice.envelope_phase == "decay":
            decay_samples = max(1, int(adsr.decay * sr))
            voice.envelope_level -= (1.0 - adsr.sustain) / decay_samples
            if voice.envelope_level <= adsr.sustain:
                voice.envelope_level = adsr.sustain
                voice.envelope_phase = "sustain"

        elif voice.envelope_phase == "sustain":
            voice.envelope_level = adsr.sustain

        elif voice.envelope_phase == "release":
            release_samples = max(1, int(adsr.release * sr))
            voice.envelope_level -= adsr.sustain / release_samples
            if voice.envelope_level <= 0.0:
                voice.envelope_level = 0.0
                voice.active = False

        return voice.envelope_level

    def all_notes_off(self) -> None:
        """kill all voices immediately."""
        self.voices.clear()


# keyboard note mapping: computer keyboard → MIDI notes
KEYBOARD_MAP: dict[str, int] = {
    # bottom row: C3 to B3
    "z": 48, "s": 49, "x": 50, "d": 51, "c": 52,
    "v": 53, "g": 54, "b": 55, "h": 56, "n": 57,
    "j": 58, "m": 59,
    # top row: C4 to B4
    "q": 60, "2": 61, "w": 62, "3": 63, "e": 64,
    "r": 65, "5": 66, "t": 67, "6": 68, "y": 69,
    "7": 70, "u": 71,
}


def key_to_note(key: str) -> int | None:
    """convert a keyboard key to a MIDI note number."""
    return KEYBOARD_MAP.get(key)
