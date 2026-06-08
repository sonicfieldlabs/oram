from __future__ import annotations

from fnmatch import fnmatchcase
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_ORIGIN_HOSTS = {"localhost", "127.0.0.1"}


def _strip_port(host: str | None) -> str:
    if not host:
        return ""
    value = host.strip().lower()
    if value.startswith("[") and "]" in value:
        return value[1 : value.index("]")]
    return value.rsplit(":", 1)[0]


def host_matches_allowed(hostname: str | None, allowed_hosts: list[str]) -> bool:
    host = (hostname or "").strip(".").lower()
    if not host:
        return False
    for allowed in allowed_hosts:
        pattern = allowed.strip().lower()
        if not pattern:
            continue
        if pattern == "*":
            return True
        if fnmatchcase(host, pattern):
            return True
    return False


def is_allowed_origin(origin: str | None, host_header: str | None, allowed_hosts: list[str]) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    origin_host = (parsed.hostname or "").strip(".").lower()
    if origin_host in LOCAL_ORIGIN_HOSTS:
        return True
    if host_matches_allowed(origin_host, allowed_hosts):
        return True
    return origin_host == _strip_port(host_header)


class LocalOriginAndHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_hosts: list[str]) -> None:
        super().__init__(app)
        self.allowed_hosts = allowed_hosts

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method.upper() not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and not is_allowed_origin(
                origin,
                request.headers.get("host"),
                self.allowed_hosts,
            ):
                return PlainTextResponse("Forbidden origin", status_code=403)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
        return response
