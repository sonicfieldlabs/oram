from __future__ import annotations

from fastapi import APIRouter

from oram_sa3_server.huggingface_access import stable_audio_hf_status


router = APIRouter()


@router.get("/huggingface/status")
def huggingface_status(check_models: bool = False) -> dict:
    return stable_audio_hf_status(check_models=check_models)
