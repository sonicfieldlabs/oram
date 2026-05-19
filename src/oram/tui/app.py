"""oram.tui.app — rich live terminal interface.

oram v2: recorder-first layout, per-layer mode indicators,
listening route display, derivation depth.

displays:
- app name, mode, listen status, route, engine, scene
- 4 layer rows with mode (R/L/S), source, derivation depth
- input/master level meters
- command line, recent log, status
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table
from rich.text import Text

from oram.archive.waveform_text import layer_to_text
from oram.tui.meters import render_meter
from oram.tui.theme import ORAM_THEME

if TYPE_CHECKING:
    from oram.audio.engine import MockAudioEngine
    from oram.audio.layer import LayerManager
    from oram.types import OramSession


# layer mode display labels
_MODE_LABELS = {
    "recorder": "rec",
    "looper": "loop",
    "sampler": "smp",
}


class OramTUI:
    """terminal user interface for oram v2."""

    def __init__(
        self,
        session: OramSession,
        layer_manager: LayerManager,
        engine: MockAudioEngine,
    ):
        self.session = session
        self.layers = layer_manager
        self.engine = engine
        self.console = Console(theme=ORAM_THEME)
        self._log: deque[str] = deque(maxlen=5)
        self._current_cmd: str = ""
        self._status: str = ""
        self._ptt_active: bool = False

    def add_log(self, message: str) -> None:
        """add a message to the log."""
        self._log.append(message)

    def set_status(self, status: str) -> None:
        """set the status line."""
        self._status = status

    def set_current_cmd(self, cmd: str) -> None:
        """set the current command display."""
        self._current_cmd = cmd

    def set_ptt(self, active: bool) -> None:
        """set push-to-talk indicator."""
        self._ptt_active = active

    def render(self) -> Table:
        """render the full TUI as a rich renderable."""
        # header
        listen = "on" if self.session.listening else "off"
        mode = self.session.mode.value
        input_mode = self.session.input_mode
        auto_listen = " auto" if self.session.auto_listen else ""
        bpm = f"bpm: {self.session.bpm:.0f}" if self.session.bpm else "bpm: --"
        scene = self.session.scene or "untitled"

        header = Text()
        header.append("oram v2", style="oram.title")
        header.append(
            " // recorder / looper / sampler",
            style="oram.mode",
        )

        subheader = Text()
        subheader.append(
            f"input: {input_mode}   listen: {listen}{auto_listen}   mode: {mode}   {bpm}   scene: {scene}",
            style="oram.mode",
        )

        # layer table
        layer_table = Table(
            show_header=False, show_edge=False, show_lines=False,
            padding=(0, 1), expand=True,
        )
        layer_table.add_column("sel", width=1)
        layer_table.add_column("id", width=3)
        layer_table.add_column("waveform", min_width=24)
        layer_table.add_column("dur", width=8)
        layer_table.add_column("mode", width=5)
        layer_table.add_column("state", width=12)
        layer_table.add_column("info", min_width=16)

        for i, layer in enumerate(self.layers.layers):
            selected = ">" if i == self.layers.selected else " "
            lid = f"L{layer.slot + 1}"
            waveform = layer_to_text(layer, width=24)
            layer_mode = _MODE_LABELS.get(layer.layer_mode.value, "rec")

            if layer.is_empty:
                dur = ""
                state = "empty"
                style = "oram.layer.empty"
            else:
                dur = f"{layer.duration_seconds:.1f}s"
                if layer.muted:
                    state = "muted"
                    style = "oram.layer.muted"
                elif layer.solo:
                    state = "solo"
                    style = "oram.layer.solo"
                elif layer.is_generated:
                    state = f"gen d{layer.generation_depth}"
                    style = "oram.layer.generated"
                else:
                    state = layer.source_type.value
                    style = "oram.layer.active"

            if i == self.layers.selected:
                style = "oram.layer.selected"

            # info column: effects + lineage
            info_parts = []
            if layer.reverse:
                info_parts.append("rev")
            if layer.parent_layer_id:
                info_parts.append(f"←{layer.parent_layer_id[-3:]}")
            if layer.effects_applied:
                info_parts.extend(layer.effects_applied[:2])
            info = " ".join(info_parts)

            layer_table.add_row(selected, lid, waveform, dur, layer_mode, state, info, style=style)

        # meters
        input_meter = render_meter(self.engine.get_input_level(), width=20, label="input   ")
        master_meter = render_meter(self.engine.get_output_level(), width=20, label="master  ")

        # command/log
        ptt_indicator = " [●]" if self._ptt_active else ""
        if self._current_cmd:
            cmd_line = f'cmd     "{self._current_cmd}"{ptt_indicator}'
        else:
            cmd_line = f"cmd     --{ptt_indicator}"

        # assemble
        output = Table(show_header=False, show_edge=False, show_lines=False, expand=True)
        output.add_column()
        output.add_row(header)
        output.add_row(subheader)
        output.add_row("")
        output.add_row(layer_table)
        output.add_row("")
        output.add_row(Text(input_meter, style="oram.meter.mid"))
        output.add_row(Text(master_meter, style="oram.meter.mid"))
        output.add_row("")
        output.add_row(Text(cmd_line, style="oram.cmd"))
        output.add_row(Text(f"log     {list(self._log)[-1] if self._log else '--'}", style="oram.log"))

        if self._status:
            output.add_row(Text(f"status  {self._status}", style="oram.status"))

        return output
