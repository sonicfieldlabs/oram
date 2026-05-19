"""oram.tui.waveform — buffer waveform summary display."""

from __future__ import annotations

from oram.archive.waveform_text import buffer_to_text, layer_to_text

# re-export for TUI use
__all__ = ["buffer_to_text", "layer_to_text"]
