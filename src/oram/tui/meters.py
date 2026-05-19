"""oram.tui.meters — level meter rendering."""

from __future__ import annotations

BLOCKS = " ▁▂▃▄▅▆▇█"


def render_meter(level: float, width: int = 20, label: str = "") -> str:
    """render a level meter as unicode blocks.

    level: 0.0 to 1.0+
    """
    level = max(0.0, min(1.0, level))
    filled = int(level * width)

    chars = []
    for i in range(width):
        if i < filled:
            block_idx = min(int((i / width) * 8) + 1, 8)
            chars.append(BLOCKS[block_idx])
        else:
            chars.append(" ")

    meter = "".join(chars)
    if label:
        return f"{label:8s}{meter}"
    return meter
