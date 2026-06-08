from __future__ import annotations

from fastapi import APIRouter

from oram_sa3_server.registry import registry
from oram_sa3_server.schemas import LoadModelRequest, LoadModelResponse, ModelsResponse


router = APIRouter()


@router.get("/models", response_model=ModelsResponse)
def list_models() -> ModelsResponse:
    return ModelsResponse(providers=registry.list_status())


@router.post("/models/load", response_model=LoadModelResponse)
def load_model(request: LoadModelRequest) -> LoadModelResponse:
    try:
        result = registry.load_model(request.provider, request.model, request.device)
        return LoadModelResponse(**result)
    except Exception as exc:
        return LoadModelResponse(
            provider=request.provider,
            model=request.model,
            device=request.device,
            status="error",
            detail=str(exc),
        )

