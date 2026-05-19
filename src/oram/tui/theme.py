"""oram.tui.theme — monochrome theme and visual vocabulary."""

from __future__ import annotations

from rich.theme import Theme

# oram's visual vocabulary
LEVEL_BLOCKS = "▁▂▃▄▅▆▇█"
LOOP_CHARS = {"active": "═", "empty": "─", "recording": "●", "muted": "░", "solo": "▓"}
GRAIN_CHARS = "░▒▓"

# monochrome-first theme
ORAM_THEME = Theme({
    "oram.title": "bold white",
    "oram.mode": "dim white",
    "oram.status": "dim white",
    "oram.layer.active": "white",
    "oram.layer.selected": "bold white",
    "oram.layer.muted": "dim white",
    "oram.layer.empty": "dim white",
    "oram.layer.recording": "bold red",
    "oram.layer.solo": "bold yellow",
    "oram.layer.generated": "dim cyan",
    "oram.meter.low": "dim green",
    "oram.meter.mid": "green",
    "oram.meter.high": "yellow",
    "oram.meter.clip": "bold red",
    "oram.cmd": "dim white",
    "oram.log": "dim white",
    "oram.error": "bold red",
    "oram.summon": "dim magenta",
})


def level_to_blocks(level: float, width: int = 20) -> str:
    """convert a level (0.0-1.0) to unicode block characters."""
    filled = int(level * width)
    filled = min(filled, width)

    blocks = []
    for i in range(width):
        if i < filled:
            # graduated blocks
            ratio = i / width
            if ratio < 0.5:
                blocks.append(LEVEL_BLOCKS[min(int(ratio * 16), 7)])
            elif ratio < 0.8:
                blocks.append(LEVEL_BLOCKS[min(int(ratio * 10), 7)])
            else:
                blocks.append(LEVEL_BLOCKS[7])
        else:
            blocks.append(" ")

    return "".join(blocks)
