"""oram.agent.controller — agent orchestration.

ties together the rule parser, optional LLM fallback, and action routing.
"""

from __future__ import annotations

from oram.command.parser import CommandParser
from oram.command.schemas import OramAction


class AgentController:
    """orchestrates command parsing with rule engine and optional LLM fallback."""

    def __init__(self, llm_adapter=None):
        self.parser = CommandParser(llm_fallback=llm_adapter)

    def process_command(self, text: str) -> OramAction:
        """process a text command and return a validated action."""
        return self.parser.parse(text)
