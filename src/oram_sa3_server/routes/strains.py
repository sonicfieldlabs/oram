from __future__ import annotations

from fastapi import APIRouter, HTTPException

from oram_sa3_server.registry import registry, strain_registry
from oram_sa3_server.schemas import StrainCard, StrainLoadRequest, StrainRegistryResponse


router = APIRouter(prefix="/strains", tags=["strains"])


@router.get("", response_model=StrainRegistryResponse)
@router.get("/", response_model=StrainRegistryResponse)
def list_strains() -> StrainRegistryResponse:
    return StrainRegistryResponse(strains=strain_registry.list_strains())


@router.post("", response_model=StrainCard)
@router.post("/", response_model=StrainCard)
def save_strain(strain: StrainCard) -> StrainCard:
    return strain_registry.save_strain(strain)


@router.get("/{strain_id}", response_model=StrainCard)
def get_strain(strain_id: str) -> StrainCard:
    try:
        return strain_registry.get_strain(strain_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"strain not found: {strain_id}") from exc


@router.delete("/{strain_id}")
def delete_strain(strain_id: str) -> dict[str, str]:
    try:
        strain_registry.delete_strain(strain_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"strain not found: {strain_id}") from exc
    return {"status": "deleted", "strain_id": strain_id}


@router.post("/load")
def load_strains(request: StrainLoadRequest) -> dict:
    try:
        paths = strain_registry.resolve_paths(request.strain_ids, request.paths)
        result = registry.get(request.provider).load_lora(paths)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"strain not found: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        return {"status": "error", "provider": request.provider, "error": str(exc)}
    return {
        **result,
        "provider": request.provider,
        "strain_ids": request.strain_ids,
        "paths": paths,
    }
