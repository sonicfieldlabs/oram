"""oram.archive.waveform_text — text waveform representation using unicode blocks."""

from __future__ import annotations

import numpy as np

from oram.types import LoopLayer

# unicode block characters for level visualization
BLOCKS = " ▁▂▃▄▅▆▇█"


def buffer_to_text(buffer: np.ndarray, width: int = 40) -> str:
    """convert an audio buffer to a text waveform using unicode blocks.

    downsamples the buffer to `width` characters and maps RMS levels
    to unicode block characters.
    """
    if buffer.shape[0] == 0:
        return "-" * width

    # mono-ize for visualization
    if buffer.ndim > 1:
        mono = np.mean(buffer, axis=1)
    else:
        mono = buffer

    # downsample to width chunks
    chunk_size = max(1, len(mono) // width)
    chars = []

    for i in range(width):
        start = i * chunk_size
        end = min(start + chunk_size, len(mono))
        if start >= len(mono):
            chars.append(" ")
            continue

        chunk = mono[start:end]
        rms = float(np.sqrt(np.mean(chunk**2)))

        # map to block index (0-8)
        idx = min(int(rms * 40), len(BLOCKS) - 1)
        chars.append(BLOCKS[idx])

    return "".join(chars)


def layer_to_text(layer: LoopLayer, width: int = 24) -> str:
    """create a text representation of a layer row."""
    if layer.is_empty:
        return "-" * width

    waveform = buffer_to_text(layer.buffer, width)
    return waveform


def session_waveform_text(layers: list[LoopLayer], width: int = 24) -> str:
    """create a full text waveform display for all layers."""
    lines = []
    for layer in layers:
        prefix = f"L{layer.slot + 1}"
        waveform = layer_to_text(layer, width)

        status_parts = []
        if layer.is_empty:
            status_parts.append("empty")
        else:
            status_parts.append(f"{layer.duration_seconds:.1f}s")
            if layer.muted:
                status_parts.append("muted")
            if layer.solo:
                status_parts.append("solo")
            if layer.reverse:
                status_parts.append("reversed")
            if layer.is_generated:
                status_parts.append("generated")
            if layer.effects_applied:
                status_parts.extend(layer.effects_applied)

        status = "  ".join(status_parts)
        lines.append(f"{prefix}  {waveform}   {status}")

    return "\n".join(lines)
