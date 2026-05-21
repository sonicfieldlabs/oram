"""Network allowlist helpers for local-first provider calls."""

from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_ALLOWED_HOSTS = {
    "api.elevenlabs.io",
    "api.stability.ai",
}


def allowed_hosts() -> set[str]:
    raw = os.environ.get("ORAM_NETWORK_ALLOWLIST", "")
    hosts = {h.strip().lower() for h in raw.split(",") if h.strip()}
    return hosts or set(DEFAULT_ALLOWED_HOSTS)


def is_url_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in allowed_hosts()
