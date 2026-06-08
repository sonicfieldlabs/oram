from __future__ import annotations

from fastapi import APIRouter

from oram_sa3_server.registry import registry, settings
from oram_sa3_server.schemas import HealthResponse


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        server=settings.server_name,
        active_provider=registry.active_provider_id,
        device=registry.active_device(),
        models_loaded=registry.loaded_models(),
        output_dir=registry.storage.relative_path(settings.output_root),
    )
