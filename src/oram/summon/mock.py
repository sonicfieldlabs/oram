"""oram.summon.mock — procedural mock sound generator.

generates synthetic textures locally without any API:
- rain -> sparse filtered noise impulses
- room tone -> low-level colored noise
- drone -> low sine/triangle layers with slow amplitude movement
- machine -> quiet periodic pulses plus filtered noise
- forest -> noise bed plus sparse chirp-like particles
"""

from __future__ import annotations

import numpy as np


class MockSoundGenerator:
    """generates procedural synthetic textures from keyword prompts."""

    def generate(self, prompt: str, duration: float, sample_rate: int) -> np.ndarray:
        """generate audio based on keywords in the prompt."""
        samples = int(duration * sample_rate)
        prompt_lower = prompt.lower()

        if "rain" in prompt_lower:
            audio = self._rain(samples, sample_rate)
        elif "drone" in prompt_lower:
            audio = self._drone(samples, sample_rate)
        elif "room" in prompt_lower or "tone" in prompt_lower:
            audio = self._room_tone(samples, sample_rate)
        elif "machine" in prompt_lower:
            audio = self._machine(samples, sample_rate)
        elif "forest" in prompt_lower:
            audio = self._forest(samples, sample_rate)
        else:
            # default: gentle noise bed
            audio = self._room_tone(samples, sample_rate)

        # make stereo
        if audio.ndim == 1:
            audio = np.column_stack([audio, audio])

        return audio.astype(np.float32)

    def _rain(self, samples: int, sr: int) -> np.ndarray:
        """sparse filtered noise impulses."""
        audio = np.zeros(samples, dtype=np.float32)
        # random impulses
        num_drops = int(samples / sr * 20)  # ~20 drops per second
        positions = np.random.randint(0, max(1, samples - 200), size=num_drops)
        for pos in positions:
            drop_len = np.random.randint(50, 200)
            end = min(pos + drop_len, samples)
            drop = np.random.randn(end - pos).astype(np.float32) * 0.05
            # simple envelope
            env = np.linspace(1.0, 0.0, end - pos, dtype=np.float32)
            audio[pos:end] += drop * env
        return audio * 0.3

    def _drone(self, samples: int, sr: int) -> np.ndarray:
        """low sine/triangle layers with slow amplitude movement."""
        t = np.arange(samples, dtype=np.float32) / sr
        # fundamental low drone
        freq = 55.0  # low A
        audio = np.sin(2 * np.pi * freq * t) * 0.2
        # add harmonics
        audio += np.sin(2 * np.pi * freq * 1.5 * t) * 0.08
        audio += np.sin(2 * np.pi * freq * 2.0 * t) * 0.05
        # slow amplitude modulation
        mod = 0.5 + 0.5 * np.sin(2 * np.pi * 0.1 * t)
        audio *= mod
        return audio * 0.4

    def _room_tone(self, samples: int, sr: int) -> np.ndarray:
        """low-level colored noise."""
        noise = np.random.randn(samples).astype(np.float32)
        # simple lowpass via averaging
        kernel_size = 32
        kernel = np.ones(kernel_size, dtype=np.float32) / kernel_size
        audio = np.convolve(noise, kernel, mode="same")
        return audio * 0.08

    def _machine(self, samples: int, sr: int) -> np.ndarray:
        """quiet periodic pulses plus filtered noise."""
        t = np.arange(samples, dtype=np.float32) / sr
        # periodic pulses at ~4 Hz
        pulse = np.sin(2 * np.pi * 4.0 * t) ** 8 * 0.15
        # filtered noise
        noise = np.random.randn(samples).astype(np.float32) * 0.03
        kernel = np.ones(16, dtype=np.float32) / 16
        noise = np.convolve(noise, kernel, mode="same")
        return (pulse + noise) * 0.3

    def _forest(self, samples: int, sr: int) -> np.ndarray:
        """noise bed plus sparse chirp-like particles (synthetic, not literal)."""
        # noise bed
        noise = np.random.randn(samples).astype(np.float32) * 0.02
        kernel = np.ones(64, dtype=np.float32) / 64
        bed = np.convolve(noise, kernel, mode="same")

        # sparse chirps
        num_chirps = int(samples / sr * 3)  # ~3 per second
        for _ in range(num_chirps):
            pos = np.random.randint(0, max(1, samples - 2000))
            chirp_len = np.random.randint(500, 2000)
            end = min(pos + chirp_len, samples)
            ct = np.arange(end - pos, dtype=np.float32) / sr
            freq = np.random.uniform(2000, 6000)
            chirp = np.sin(2 * np.pi * (freq + ct * 1000) * ct) * 0.03
            env = np.exp(-ct * 15)
            bed[pos:end] += chirp * env

        return bed * 0.5
