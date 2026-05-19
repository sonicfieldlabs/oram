"""oram.archive.lineage — sonic genealogy tracking.

builds and exports the lineage graph that tracks how each layer
was derived from others through listening and generation.
"""

from __future__ import annotations

import json
from pathlib import Path

from oram.types import Layer, LineageNode


def build_lineage(layers: list[Layer]) -> list[dict]:
    """build the lineage graph from a list of layers."""
    nodes = []
    for layer in layers:
        if layer.is_empty:
            continue
        node = {
            "id": layer.id,
            "slot": layer.slot + 1,
            "name": layer.name,
            "type": layer.source_type.value,
            "parent": layer.parent_layer_id,
            "route": layer.listening_route.value if layer.parent_layer_id else None,
            "engine": layer.generation_engine.value if layer.is_generated else None,
            "prompt": layer.generation_prompt,
            "depth": layer.generation_depth,
            "duration": layer.duration_seconds,
            "effects": layer.effects_applied.copy(),
        }
        nodes.append(node)
    return nodes


def get_chain(layer_id: str, layers: list[Layer]) -> list[LineageNode]:
    """get the derivation chain for a layer (oldest first)."""
    layer_map = {l.id: l for l in layers}
    chain = []
    current_id: str | None = layer_id

    while current_id and current_id in layer_map:
        l = layer_map[current_id]
        chain.append(LineageNode(
            id=l.id,
            type=l.source_type.value,
            parent=l.parent_layer_id,
            route=l.listening_route.value if l.parent_layer_id else None,
            engine=l.generation_engine.value if l.is_generated else None,
            prompt=l.generation_prompt,
            depth=l.generation_depth,
        ))
        current_id = l.parent_layer_id

    chain.reverse()
    return chain


def save_lineage(layers: list[Layer], path: Path) -> None:
    """save the lineage graph to a JSON file."""
    data = {
        "version": "2.0",
        "nodes": build_lineage(layers),
        "chains": {},
    }

    # build chains for each generated layer
    for layer in layers:
        if layer.is_generated and not layer.is_empty:
            chain = get_chain(layer.id, layers)
            data["chains"][layer.id] = [
                {"id": n.id, "type": n.type, "depth": n.depth}
                for n in chain
            ]

    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def format_lineage_text(layers: list[Layer]) -> str:
    """format the lineage as a text tree for TUI display."""
    roots = [l for l in layers if not l.is_empty and not l.parent_layer_id]
    children_map: dict[str, list[Layer]] = {}
    for l in layers:
        if l.parent_layer_id and not l.is_empty:
            children_map.setdefault(l.parent_layer_id, []).append(l)

    lines = []
    for root in roots:
        lines.append(f"L{root.slot + 1} [{root.source_type.value}] {root.name}")
        _format_children(root, children_map, lines, indent=1)

    return "\n".join(lines) if lines else "no layers"


def _format_children(parent: Layer, children_map: dict[str, list[Layer]], lines: list[str], indent: int) -> None:
    children = children_map.get(parent.id, [])
    for child in children:
        prefix = "  " * indent + "└─ "
        route = child.listening_route.value if child.parent_layer_id else ""
        engine = child.generation_engine.value if child.is_generated else ""
        label = f"{prefix}L{child.slot + 1} [{child.source_type.value}]"
        lines.append(f"{label} d{child.generation_depth} ({route}→{engine})")
        _format_children(child, children_map, lines, indent + 1)
