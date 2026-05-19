"""oram.command.parser — priority parser chain.

1. normalize text
2. extract entities (layer refs, durations, semitones)
3. match deterministic rules
4. optional LLM fallback (if configured)
5. reject if still unrecognized
"""

from __future__ import annotations

from pydantic import ValidationError

from oram.command.grammar import match_rules
from oram.command.schemas import OramAction, UnknownAction


class CommandParser:
    """parses text commands into structured oram actions."""

    def __init__(self, llm_fallback=None):
        self._llm_fallback = llm_fallback

    def parse(self, text: str) -> OramAction:
        """parse a text command into a structured action.

        priority:
        1. deterministic rule matching
        2. LLM fallback (if enabled and rules didn't match)
        3. UnknownAction
        """
        if not text or not text.strip():
            return UnknownAction(reason="empty command", raw_text=text)

        # try deterministic rules first
        try:
            result = match_rules(text)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            message = first.get("msg", "invalid command")
            return UnknownAction(reason=f"invalid command: {message}", raw_text=text)

        # if rules didn't match and LLM fallback is available, try it
        if isinstance(result, UnknownAction) and self._llm_fallback is not None:
            llm_result = self._llm_fallback.parse(text)
            if llm_result is not None and not isinstance(llm_result, UnknownAction):
                return llm_result

        return result
