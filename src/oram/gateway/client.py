"""oram.gateway.client — shared ElevenLabs HTTP client.

centralizes API key handling, timeout, retry policy, response error
formatting, and output format management for all ElevenLabs adapters.

SAFETY:
- __repr__ never exposes the API key.
- _redact() strips credentials from headers before logging.
- async_post_json() available for FastAPI handlers (uses asyncio.sleep).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

API_BASE = "https://api.elevenlabs.io/v1"

# retry on these status codes
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0  # seconds, doubles each retry

# headers that must never appear in logs
_REDACT_KEYS = {"xi-api-key", "authorization", "x-api-key"}


def _redact(headers: dict[str, str]) -> dict[str, str]:
    """return a copy of headers with credential values masked."""
    return {
        k: ("***" if k.lower() in _REDACT_KEYS else v)
        for k, v in headers.items()
    }


class ElevenLabsHTTPClient:
    """shared HTTP client for all ElevenLabs API adapters."""

    def __init__(
        self,
        api_key: str,
        timeout: float = 120.0,
        output_format: str | None = None,
    ):
        self._api_key = api_key
        self._timeout = timeout
        self._output_format = output_format

    def __repr__(self) -> str:
        return "<ElevenLabsHTTPClient api_key=***>"

    @property
    def headers(self) -> dict[str, str]:
        h = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        return h

    def post_json(
        self,
        path: str,
        body: dict,
        timeout: float | None = None,
    ) -> httpx.Response:
        """POST JSON to an ElevenLabs endpoint with retry (sync, for workers)."""
        url = f"{API_BASE}{path}"
        to = timeout or self._timeout
        last_exc = None

        for attempt in range(_MAX_RETRIES):
            try:
                params: dict[str, Any] | None = None
                if self._output_format:
                    params = {"output_format": self._output_format}
                resp = httpx.post(
                    url,
                    headers=self.headers,
                    json=body,
                    params=params,
                    timeout=to,
                )
                if resp.status_code not in _RETRY_STATUSES:
                    resp.raise_for_status()
                    return resp
                # retryable
                last_exc = httpx.HTTPStatusError(
                    message=f"{resp.status_code} {resp.reason_phrase}: {resp.text[:200]}",
                    request=resp.request,
                    response=resp,
                )
            except httpx.TimeoutException as e:
                last_exc = e
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in _RETRY_STATUSES:
                    raise
                last_exc = e

            wait = _RETRY_BACKOFF * (2 ** attempt)
            time.sleep(wait)

        raise last_exc or RuntimeError("request failed after retries")

    async def async_post_json(
        self,
        path: str,
        body: dict,
        timeout: float | None = None,
    ) -> httpx.Response:
        """POST JSON with retry (async, for FastAPI handlers).

        uses asyncio.sleep instead of time.sleep to avoid blocking the
        event loop during retry backoff.
        """
        url = f"{API_BASE}{path}"
        to = timeout or self._timeout
        last_exc = None

        async with httpx.AsyncClient() as client:
            for attempt in range(_MAX_RETRIES):
                try:
                    params: dict[str, Any] | None = None
                    if self._output_format:
                        params = {"output_format": self._output_format}
                    resp = await client.post(
                        url,
                        headers=self.headers,
                        json=body,
                        params=params,
                        timeout=to,
                    )
                    if resp.status_code not in _RETRY_STATUSES:
                        resp.raise_for_status()
                        return resp
                    last_exc = httpx.HTTPStatusError(
                        message=f"{resp.status_code} {resp.reason_phrase}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
                except httpx.TimeoutException as e:
                    last_exc = e
                except httpx.HTTPStatusError as e:
                    if e.response.status_code not in _RETRY_STATUSES:
                        raise
                    last_exc = e

                wait = _RETRY_BACKOFF * (2 ** attempt)
                await asyncio.sleep(wait)

        raise last_exc or RuntimeError("request failed after retries")

    def post_multipart(
        self,
        path: str,
        files: dict,
        data: dict | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """POST multipart form data with retry."""
        url = f"{API_BASE}{path}"
        to = timeout or self._timeout
        headers = {"xi-api-key": self._api_key}

        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                params: dict[str, Any] | None = None
                if self._output_format:
                    params = {"output_format": self._output_format}
                resp = httpx.post(
                    url,
                    headers=headers,
                    files=files,
                    data=data or {},
                    params=params,
                    timeout=to,
                )
                if resp.status_code not in _RETRY_STATUSES:
                    resp.raise_for_status()
                    return resp
                last_exc = httpx.HTTPStatusError(
                    message=f"{resp.status_code} {resp.reason_phrase}: {resp.text[:200]}",
                    request=resp.request,
                    response=resp,
                )
            except httpx.TimeoutException as e:
                last_exc = e
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in _RETRY_STATUSES:
                    raise
                last_exc = e

            wait = _RETRY_BACKOFF * (2 ** attempt)
            time.sleep(wait)

        raise last_exc or RuntimeError("request failed after retries")

    @staticmethod
    def parse_cost_header(response: httpx.Response) -> float | None:
        """extract character cost from response headers."""
        cost = response.headers.get("character-cost") or response.headers.get("x-character-count")
        if cost:
            try:
                return float(cost)
            except ValueError:
                pass
        return None

    @staticmethod
    def format_error(response: httpx.Response) -> str:
        """format an API error response for logging."""
        try:
            data = response.json()
            detail = data.get("detail", {})
            if isinstance(detail, dict):
                return detail.get("message", str(response.status_code))
            return str(detail)
        except Exception:
            return f"HTTP {response.status_code}"
