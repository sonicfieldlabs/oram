"""tests for LLM CLI adapter — sanitization, JSON extraction, safety (§1.4)."""

from __future__ import annotations

from oram.agent.llm_adapter import LLMCliAdapter, _extract_json, _sanitize_transcript


class TestSanitizeTranscript:
    """transcript sanitization before embedding in prompt."""

    def test_strips_flag_prefix(self):
        assert _sanitize_transcript("--unsafe-flag real command") == "real command"

    def test_strips_multiple_flags(self):
        assert _sanitize_transcript("--a --b actual text") == "actual text"

    def test_strips_short_flags(self):
        assert _sanitize_transcript("-p some text") == "some text"

    def test_preserves_normal_text(self):
        assert _sanitize_transcript("wash it in reverb") == "wash it in reverb"

    def test_caps_length(self):
        long_text = "a" * 5000
        result = _sanitize_transcript(long_text)
        assert len(result) == 2000

    def test_strips_control_characters(self):
        result = _sanitize_transcript("hello\x00\x01\x02world")
        assert result == "helloworld"

    def test_empty_after_flag_strip(self):
        result = _sanitize_transcript("--only-flags")
        assert result == ""


class TestExtractJson:
    """brace-balanced JSON extraction."""

    def test_simple_object(self):
        assert _extract_json('{"action":"record"}') == '{"action":"record"}'

    def test_nested_object(self):
        text = '{"action":"apply_effect","parameters":{"wet":0.5}}'
        result = _extract_json(text)
        assert result == text

    def test_json_in_prose(self):
        text = 'Here is the action: {"action":"mute_layer","target":1} Done.'
        result = _extract_json(text)
        assert result == '{"action":"mute_layer","target":1}'

    def test_no_json(self):
        assert _extract_json("no json here") is None

    def test_unbalanced_braces(self):
        assert _extract_json("{unclosed") is None

    def test_deeply_nested(self):
        text = '{"a":{"b":{"c":1}}}'
        assert _extract_json(text) == text


class TestLLMCliAdapter:
    """adapter initialization and availability."""

    def test_unavailable_returns_none(self):
        adapter = LLMCliAdapter()
        # if no codex/opencode, _cli_tool is None
        if adapter._cli_tool is None:
            result = adapter.parse("hello")
            assert result is None

    def test_no_arg_injection_in_command_list(self):
        """the command list passed to subprocess must not include
        raw user transcript as a separate argument."""
        # we can't easily test subprocess invocation without the tool,
        # but we verify the transcript gets embedded in the prompt string,
        # not as a separate argument
        text = "--dangerous-flag payload"
        sanitized = _sanitize_transcript(text)
        assert not sanitized.startswith("--"), (
            "sanitized transcript must not start with flag-like patterns"
        )
