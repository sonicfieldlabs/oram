"""oram.audio.export — WAV export via soundfile."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from oram.audio.layer import LayerManager
from oram.audio.mixer import Mixer


def export_mix(
    layer_manager: LayerManager,
    output_path: Path,
    sample_rate: int = 48000,
) -> Path:
    """export the full mix as a stereo WAV file.

    renders all active layers through the mixer.
    """
    mixer = Mixer(sample_rate=sample_rate)
    active = layer_manager.get_active_layers()

    if not active:
        # write silence
        silence = np.zeros((sample_rate, 2), dtype=np.float32)
        sf.write(str(output_path), silence, sample_rate, subtype="FLOAT")
        return output_path

    # find the longest layer to determine mix length
    max_length = max(l.length_samples for l in active)

    # §3.2: pre-allocate full output array instead of collecting blocks
    output = np.zeros((max_length, 2), dtype=np.float32)
    block_size = 512

    # save and reset playheads
    saved_playheads = {l.id: l.playhead for l in active}
    for l in active:
        l.playhead = 0

    rendered = 0
    while rendered < max_length:
        remaining = max_length - rendered
        current_block = min(block_size, remaining)
        block = mixer.mix_block(active, current_block)
        output[rendered:rendered + current_block] = block
        rendered += current_block

        # advance playheads
        mixer.advance_playheads(active, current_block)

    # restore playheads
    for l in active:
        l.playhead = saved_playheads.get(l.id, 0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), output, sample_rate, subtype="FLOAT")
    return output_path


def export_stem(
    layer_manager: LayerManager,
    layer_id: int,
    output_path: Path,
    sample_rate: int = 48000,
) -> Path | None:
    """export a single layer as a WAV file."""
    layer = layer_manager.get_layer(layer_id)
    if layer.is_empty:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), layer.buffer, sample_rate, subtype="FLOAT")
    return output_path
