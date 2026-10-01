"""Network allowlist helpers for local-first provider calls."""

from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_ALLOWED_HOSTS = {
    "api.elevenlabs.io",
    "api.stability.ai",
    "fal.run",
    "v3.fal.media",
    "storage.googleapis.com",
}


def allowed_hosts() -> set[str]:
    raw = os.environ.get("ORAM_NETWORK_ALLOWLIST", "")
    hosts = {h.strip().lower() for h in raw.split(",") if h.strip()}
    return hosts or set(DEFAULT_ALLOWED_HOSTS)


def is_url_allowed(url: str, *, allow_local: bool = False) -> bool:
    """Cloud URLs use HTTPS; HTTP is restricted to explicitly local adapters."""
    if not isinstance(url, str) or not url or any(ord(c) <= 32 for c in url) or "\\" in url:
        return False
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    if not host or parsed.username is not None or parsed.password is not None or parsed.fragment:
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    local = {"127.0.0.1", "localhost", "::1"}
    explicit = {h.strip().lower() for h in os.environ.get("ORAM_NETWORK_ALLOWLIST", "").split(",") if h.strip()}
    if parsed.scheme == "http":
        return allow_local and (host in local or host in explicit)
    return parsed.scheme == "https" and (host in allowed_hosts() or allow_local and host in local)
