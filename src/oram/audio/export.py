"""oram.audio.export — WAV export via soundfile.

the mix renderer works on shallow layer proxies with private playheads, so
exporting during a live performance never touches the positions the audio
callback is reading (the old version rewound live layers mid-playback).
"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import soundfile as sf

from oram.audio.layer import LayerManager
from oram.audio.mixer import Mixer, MixerWorkspace

# 24-bit PCM keeps exports compatible with every editor/DAW at full quality
_EXPORT_SUBTYPE = "PCM_24"


def _render_proxies(layers, sample_rate: int, max_length: int) -> np.ndarray:
    """render a mix of layer proxies from position zero, region-aware."""
    mixer = Mixer(sample_rate=sample_rate)
    workspace = MixerWorkspace.create(512, channels=2)
    output = np.zeros((max_length, 2), dtype=np.float32)

    rendered = 0
    while rendered < max_length:
        current_block = min(512, max_length - rendered)
        block = mixer.mix_block_and_advance(layers, current_block, workspace=workspace)
        output[rendered:rendered + current_block] = block[:current_block]
        rendered += current_block
    return output


def export_mix(
    layer_manager: LayerManager,
    output_path: Path,
    sample_rate: int = 44100,
) -> Path:
    """export the full mix as a stereo WAV file.

    renders all audible layers through the mixer without disturbing live
    playback state.
    """
    active = layer_manager.get_active_layers()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not active:
        # write silence
        silence = np.zeros((sample_rate, 2), dtype=np.float32)
        sf.write(str(output_path), silence, sample_rate, subtype=_EXPORT_SUBTYPE)
        return output_path

    # shallow proxies share the audio buffers but own their playhead state
    proxies = []
    for layer in active:
        proxy = copy.copy(layer)
        proxy.playhead = 0
        proxy._phase = 0.0
        proxies.append(proxy)

    max_length = max(l.length_samples for l in proxies)
    output = _render_proxies(proxies, sample_rate, max_length)

    sf.write(str(output_path), output, sample_rate, subtype=_EXPORT_SUBTYPE)
    return output_path


def export_stem(
    layer_manager: LayerManager,
    layer_id: int,
    output_path: Path,
    sample_rate: int = 44100,
) -> Path | None:
    """export a single layer as a WAV file."""
    layer = layer_manager.get_layer(layer_id)
    if layer.is_empty:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), layer.buffer, sample_rate, subtype=_EXPORT_SUBTYPE)
    return output_path
