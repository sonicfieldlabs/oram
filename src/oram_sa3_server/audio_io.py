from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


WAV_WRITE_CHUNK_FRAMES = 16_384


def write_sine_wav(
    path: str | Path,
    *,
    duration: float,
    sample_rate: int = 44100,
    frequency: float = 440.0,
    amplitude: float = 0.12,
    channels: int = 2,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(max(duration, 0.01) * sample_rate)
    max_amp = int(32767 * max(0.0, min(amplitude, 1.0)))

    pack = struct.Struct("<h").pack
    angular = 2.0 * math.pi * frequency

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for start in range(0, frames, WAV_WRITE_CHUNK_FRAMES):
            stop = min(frames, start + WAV_WRITE_CHUNK_FRAMES)
            buffer = bytearray((stop - start) * channels * 2)
            offset = 0
            for idx in range(start, stop):
                frame = pack(int(max_amp * math.sin(angular * idx / sample_rate))) * channels
                buffer[offset : offset + len(frame)] = frame
                offset += len(frame)
            wav.writeframesraw(buffer)


def write_silence_wav(
    path: str | Path,
    *,
    duration: float,
    sample_rate: int = 44100,
    channels: int = 2,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(max(duration, 0.01) * sample_rate)
    chunk = (struct.pack("<h", 0) * channels) * WAV_WRITE_CHUNK_FRAMES

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        remaining = frames
        while remaining > 0:
            chunk_frames = min(remaining, WAV_WRITE_CHUNK_FRAMES)
            wav.writeframesraw(chunk[: chunk_frames * channels * 2])
            remaining -= chunk_frames


def wav_duration_seconds(path: str | Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())
