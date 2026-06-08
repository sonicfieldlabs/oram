from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from oram_sa3_server.routes._utils import (
    cleanup_transient_uploads,
    parse_ranges_text,
    payload_from_json_or_form,
    pop_transient_upload_paths,
    run_provider_method,
)
from oram_sa3_server.schemas import GenerationResult, InpaintRequest


router = APIRouter()


@router.post("/inpaint", response_model=GenerationResult)
async def inpaint(request: Request) -> GenerationResult:
    payload = await payload_from_json_or_form(request)
    transient_upload_paths = pop_transient_upload_paths(payload)
    try:
        try:
            if "inpaint_ranges" in payload and isinstance(payload["inpaint_ranges"], str):
                payload["inpaint_ranges"] = parse_ranges_text(payload["inpaint_ranges"])
            model = InpaintRequest(**payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return run_provider_method(model, "inpainting", "inpaint")
    finally:
        cleanup_transient_uploads(transient_upload_paths)
