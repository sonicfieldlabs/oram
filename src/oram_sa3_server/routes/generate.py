from __future__ import annotations

from fastapi import APIRouter

from oram_sa3_server.routes._utils import run_provider_method
from oram_sa3_server.schemas import GenerateRequest, GenerationResult


router = APIRouter()


@router.post("/generate", response_model=GenerationResult)
def generate(request: GenerateRequest) -> GenerationResult:
    return run_provider_method(request, "text-to-audio", "generate")

