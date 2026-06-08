from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable

from oram_sa3_server.config import Settings
from oram_sa3_server.storage import StorageManager


class JobRunner:
    def __init__(self, settings: Settings, storage: StorageManager) -> None:
        self.storage = storage
        self.executor = ThreadPoolExecutor(
            max_workers=settings.job_workers,
            thread_name_prefix="germinator-job",
        )
        self._lock = Lock()
        self._futures: dict[str, Future[Any]] = {}

    def submit(self, runner_job_id: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        future = self.executor.submit(fn, *args, **kwargs)
        with self._lock:
            self._futures[runner_job_id] = future
        future.add_done_callback(lambda _future: self._forget(runner_job_id))

    def cancel(self, job_id: str) -> dict[str, str | bool]:
        with self._lock:
            future = self._futures.get(job_id)
        if future is None:
            job = self.storage.get_job(job_id)
            if job is None:
                return {"cancelled": False, "status": "missing"}
            return {"cancelled": False, "status": job.status}
        if future.cancel():
            self.storage.update_job(
                job_id,
                status="cancelled",
                error="job cancelled before execution",
            )
            self._forget(job_id)
            return {"cancelled": True, "status": "cancelled"}
        return {"cancelled": False, "status": "running"}

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)
