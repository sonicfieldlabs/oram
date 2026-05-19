"""oram.gateway.usage — generation cost tracking.

tracks ElevenLabs API usage per engine, per session, per layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class UsageEntry:
    """single generation usage record."""

    timestamp: str
    engine: str
    layer_id: str
    prompt: str
    duration_seconds: float
    credits_used: float
    parameters: dict = field(default_factory=dict)


@dataclass
class UsageTracker:
    """tracks cumulative usage across a session."""

    entries: list[UsageEntry] = field(default_factory=list)

    @property
    def total_credits(self) -> float:
        return sum(e.credits_used for e in self.entries)

    @property
    def credits_by_engine(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for entry in self.entries:
            result[entry.engine] = result.get(entry.engine, 0) + entry.credits_used
        return result

    def record(
        self,
        engine: str,
        layer_id: str,
        prompt: str,
        duration_seconds: float,
        credits_used: float,
        parameters: dict | None = None,
    ) -> UsageEntry:
        """record a generation event."""
        entry = UsageEntry(
            timestamp=datetime.now().isoformat(),
            engine=engine,
            layer_id=layer_id,
            prompt=prompt,
            duration_seconds=duration_seconds,
            credits_used=credits_used,
            parameters=parameters or {},
        )
        self.entries.append(entry)
        return entry

    def save(self, path: Path) -> None:
        """save usage log to JSON."""
        data = {
            "total_credits": self.total_credits,
            "by_engine": self.credits_by_engine,
            "entries": [
                {
                    "timestamp": e.timestamp,
                    "engine": e.engine,
                    "layer_id": e.layer_id,
                    "prompt": e.prompt[:100],
                    "duration_seconds": e.duration_seconds,
                    "credits_used": e.credits_used,
                }
                for e in self.entries
            ],
        }
        path.write_text(json.dumps(data, indent=2))

    def summary(self) -> str:
        """human-readable usage summary."""
        total = self.total_credits
        by_eng = self.credits_by_engine
        parts = [f"total: {total:.0f} credits"]
        for eng, credits in sorted(by_eng.items()):
            parts.append(f"{eng}: {credits:.0f}")
        return " | ".join(parts)
