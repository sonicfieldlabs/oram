"""oram.agent.llm_adapter — LLM fallback via CLI providers.

uses locally available CLI tools (codex, opencode, or similar)
that are already authenticated on the system. no API keys needed.

SAFETY:
- user transcript is length-capped and sanitized before embedding in prompt.
- '--' prefixed strings at the start of text are stripped.
- JSON extraction uses brace-balanced scanning, not greedy regex.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from oram.command.schemas import VALID_EFFECTS, OramAction, UnknownAction

# max characters from user transcript to embed in the prompt
_MAX_PROMPT_LEN = 2000

# reject text that starts with flag-like patterns
_FLAG_PREFIX = re.compile(r"^--?\w")

# action schema description for LLM prompting
ACTION_SCHEMA_PROMPT = """
Translate this performer command into one ORAM action.
Return only valid JSON.
If the command is unsafe, ambiguous, or outside the allowed actions, return:
{"action":"unknown","reason":"..."}

Allowed actions and their schemas:
- {"action":"record","target":"selected","duration":8.0,"overdub":false}
- {"action":"stop_recording"}
- {"action":"overdub","target":"selected","duration":null}
- {"action":"select_layer","target":1}  (target: 1-4)
- {"action":"mute_layer","target":1}
- {"action":"solo_layer","target":1}
- {"action":"clear_layer","target":1}
- {"action":"set_volume","target":1,"volume":0.5}  (0.0-2.0)
- {"action":"set_pan","target":1,"pan":0.0}  (-1.0 to 1.0)
- {"action":"apply_effect","target":1,"effect":"reverse","parameters":{}}
  Effects: reverse, speed, pitch, lowpass, highpass, reverb,
  granular, fade_in, fade_out, trim_start, trim_end, spatial_far
  Parameters: speed(0.25-4.0), semitones(-12 to 12),
  cutoff_hz(20-20000), wet(0.0-1.0), decay(short/medium/long),
  density(0.0-1.0), grain_size_ms(10-500), jitter(0.0-1.0)
- {"action":"generate_layer","prompt":"...","duration":16}
- {"action":"analyze_mix"}
- {"action":"save_session"}
- {"action":"export_mix"}
- {"action":"set_mode","mode":"loop"}  (listen/record/loop/shape/summon/sleep)
- {"action":"quit"}

Current context:
"""


def _sanitize_transcript(text: str) -> str:
    """sanitize user transcript for safe embedding in an LLM prompt.

    - strips leading '--' flag-like prefixes
    - caps length at _MAX_PROMPT_LEN
    - strips control characters
    """
    # strip control characters (keep printable + whitespace)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # strip leading flag-like arguments
    text = text.strip()
    while _FLAG_PREFIX.match(text):
        # remove the first word
        text = text.split(None, 1)[-1] if " " in text else ""
        text = text.strip()
    # cap length
    if len(text) > _MAX_PROMPT_LEN:
        text = text[:_MAX_PROMPT_LEN]
    return text


def _extract_json(blob: str) -> str | None:
    """extract the first brace-balanced JSON object from text.

    uses a depth counter instead of greedy regex — handles nested
    objects and arrays correctly.
    """
    depth = 0
    start = -1
    for i, ch in enumerate(blob):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return blob[start : i + 1]
    return None


class LLMCliAdapter:
    """LLM fallback using CLI tools already available on the system."""

    def __init__(self):
        # detect available CLI tool
        self._cli_tool = self._detect_tool()

    def _detect_tool(self) -> str | None:
        """detect which CLI LLM tool is available."""
        for tool in ["codex", "opencode"]:
            if shutil.which(tool):
                return tool
        return None

    @property
    def is_available(self) -> bool:
        return self._cli_tool is not None

    def parse(self, text: str, context: str = "") -> OramAction | None:
        """ask the LLM to parse a command into an action.

        returns None if the LLM is not available or fails.
        """
        if not self._cli_tool:
            return None

        text = _sanitize_transcript(text)
        if not text:
            return UnknownAction(reason="empty transcript after sanitization")

        prompt = (
            f"{ACTION_SCHEMA_PROMPT}\n{context}\n\n"
            f"Performer command: \"{text}\"\n\nReturn only JSON:"
        )

        try:
            result = subprocess.run(
                [self._cli_tool, "--quiet", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if result.returncode != 0:
                return UnknownAction(reason=f"LLM process exited {result.returncode}")

            output = result.stdout.strip()
            # extract JSON from output using brace-balanced scanner
            json_str = _extract_json(output)
            if not json_str:
                return UnknownAction(reason="no JSON found in LLM output")

            data = json.loads(json_str)

            # validate against our schema
            return self._validate_action(data)

        except subprocess.TimeoutExpired:
            return UnknownAction(reason="LLM timed out after 15s")
        except json.JSONDecodeError as e:
            return UnknownAction(reason=f"invalid JSON from LLM: {e}")
        except KeyboardInterrupt:
            return None
        except Exception as e:
            return UnknownAction(reason=f"LLM error: {e}")

    def complete(self, prompt: str) -> str | None:
        """free-form text completion (for listening routes).

        unlike parse(), this returns the raw text response without
        expecting JSON structure.
        """
        if not self._cli_tool:
            return None

        # cap prompt length for safety
        if len(prompt) > _MAX_PROMPT_LEN * 2:
            prompt = prompt[:_MAX_PROMPT_LEN * 2]

        try:
            result = subprocess.run(
                [self._cli_tool, "--quiet", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                return None

            output = result.stdout.strip()
            return output if output else None

        except subprocess.TimeoutExpired:
            return None
        except KeyboardInterrupt:
            return None
        except Exception:
            return None

    def _validate_action(self, data: dict) -> OramAction | None:
        """validate LLM output against our action schemas."""
        from pydantic import TypeAdapter, ValidationError

        from oram.command.schemas import OramAction as OramActionType

        try:
            adapter = TypeAdapter(OramActionType)
            action = adapter.validate_python(data)

            # extra validation for effects
            if hasattr(action, "effect"):
                if action.effect not in VALID_EFFECTS:
                    return UnknownAction(reason=f"invalid effect: {action.effect}")

            return action
        except (ValidationError, Exception):
            return None
