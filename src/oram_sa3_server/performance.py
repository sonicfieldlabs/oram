from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class PerformanceMonitor:
    def __init__(self, max_events: int = 300) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = Lock()

    def record(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
        by_path: dict[str, dict[str, Any]] = {}
        for event in events:
            key = f"{event['method']} {event['path']}"
            bucket = by_path.setdefault(
                key,
                {
                    "count": 0,
                    "total_ms": 0.0,
                    "max_ms": 0.0,
                    "errors": 0,
                },
            )
            bucket["count"] += 1
            bucket["total_ms"] += event["duration_ms"]
            bucket["max_ms"] = max(bucket["max_ms"], event["duration_ms"])
            if event["status_code"] >= 500:
                bucket["errors"] += 1

        summary = []
        for route, bucket in sorted(by_path.items()):
            count = bucket["count"]
            summary.append(
                {
                    "route": route,
                    "count": count,
                    "avg_ms": round(bucket["total_ms"] / count, 3) if count else 0.0,
                    "max_ms": round(bucket["max_ms"], 3),
                    "errors": bucket["errors"],
                }
            )
        return {
            "count": len(events),
            "recent": events[-50:],
            "summary": summary,
        }


performance_monitor = PerformanceMonitor()


class PerformanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000.0
            performance_monitor.record(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 3),
                    "error": True,
                }
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000.0
        rounded_ms = round(duration_ms, 3)
        response.headers["X-Process-Time-ms"] = f"{rounded_ms:.3f}"
        performance_monitor.record(
            {
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": rounded_ms,
            }
        )
        return response
