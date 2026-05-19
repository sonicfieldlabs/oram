"""oram.agent.rules — deterministic rule definitions (delegates to grammar)."""

from __future__ import annotations

from oram.command.grammar import match_rules

# re-export for clean API
__all__ = ["match_rules"]
