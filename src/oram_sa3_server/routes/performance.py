from __future__ import annotations

from fastapi import APIRouter

from oram_sa3_server.performance import performance_monitor


router = APIRouter()


@router.get("/performance")
def performance_snapshot() -> dict:
    return performance_monitor.snapshot()
