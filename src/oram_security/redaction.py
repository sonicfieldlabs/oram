"""Central redaction helpers for logs, state, and archives."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTION = "[redacted]"

SECRET_KEYWORDS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "token",
    "xi-api-key",
    "x-api-key",
    "elevenlabs_api_key",
    "stability_api_key",
    "dashboard_token",
    "hf_token",
    "fal_key",
    "replicate_api_token",
}

PATTERNS = [
    re.compile(r"(?i)(xi-api-key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(sk-[A-Za-z0-9_-]{8,})"),
    re.compile(r"(el_[A-Za-z0-9_-]{8,})"),
]


def redact_text(value: Any) -> str:
    """Return a string with common credential shapes masked."""

    text = "" if value is None else str(value)
    for pattern in PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda _m: f"credential={REDACTION}", text)
        else:
            text = pattern.sub(REDACTION, text)
    return text


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in SECRET_KEYWORDS or normalized.endswith("_token") or normalized.endswith("_api_key")


def redact_mapping(value: Any) -> Any:
    """Recursively redact secret-like mapping values."""

    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            if _is_secret_key(str(key)):
                result["credential"] = REDACTION
            else:
                result[key] = redact_mapping(child)
        return result
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
