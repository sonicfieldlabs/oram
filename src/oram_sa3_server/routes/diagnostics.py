from __future__ import annotations

from fastapi import APIRouter

from oram_sa3_server.diagnostics import environment_report
from oram_sa3_server.registry import registry, settings


router = APIRouter()


@router.get("/diagnostics")
def diagnostics() -> dict:
    return environment_report(settings, registry)

