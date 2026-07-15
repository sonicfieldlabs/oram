from __future__ import annotations

import logging

from fastapi import APIRouter

from oram_sa3_server.registry import registry
from oram_sa3_server.schemas import LoraLoadRequest, LoraStrengthRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/lora/load")
def load_lora(request: LoraLoadRequest) -> dict:
    try:
        return registry.get(request.provider).load_lora(request.paths)
    except Exception:
        logger.exception("LoRA load failed")
        return {"status": "error", "provider": request.provider, "error": "LoRA load failed"}


@router.post("/lora/strength")
def set_lora_strength(request: LoraStrengthRequest) -> dict:
    try:
        provider = registry.get(request.provider)
        return provider.set_lora_strength(request.strength, request.lora_index)
    except Exception:
        logger.exception("LoRA strength update failed")
        return {"status": "error", "provider": request.provider, "error": "LoRA strength update failed"}
