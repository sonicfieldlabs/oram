from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from oram_sa3_server.routes._utils import (
    cleanup_transient_uploads,
    payload_from_json_or_form,
    pop_transient_upload_paths,
    run_provider_method,
)
from oram_sa3_server.schemas import AudioToAudioRequest, GenerationResult

router = APIRouter()


@router.post("/audio-to-audio", response_model=GenerationResult)
async def audio_to_audio(request: Request) -> GenerationResult:
    payload = await payload_from_json_or_form(request)
    transient_upload_paths = pop_transient_upload_paths(payload)
    try:
        try:
            model = AudioToAudioRequest(**payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc
        # inference blocks (subprocess up to 1800s) — keep it off the loop so
        # /health, /jobs, and the events websocket stay responsive
        return await run_in_threadpool(
            run_provider_method, model, "audio-to-audio", "audio_to_audio"
        )
    finally:
        cleanup_transient_uploads(transient_upload_paths)
