from __future__ import annotations

from oram_sa3_server.config import Settings
from oram_sa3_server.providers.base import AudioGenerationProvider
from oram_sa3_server.schemas import (
    AudioToAudioRequest,
    ContinueRequest,
    GenerateRequest,
    GenerationResult,
    InpaintRequest,
)


class StabilityAPIProvider(AudioGenerationProvider):
    provider_id = "stability_api"

    def __init__(self, storage, settings: Settings) -> None:
        super().__init__(storage)
        self.settings = settings

    def is_available(self) -> bool:
        self.last_error = "Future provider stub. Local generation does not require an API key."
        return False

    def list_models(self) -> list[str]:
        return ["large"]

    def load_model(self, model_id: str, device: str = "auto") -> dict:
        self.loaded_model_id = model_id
        self.current_device = "api"
        return {
            "provider": self.provider_id,
            "model": model_id,
            "device": self.current_device,
            "status": "stub",
            "detail": self.last_error,
        }

    def generate(self, request: GenerateRequest) -> GenerationResult:
        return self._stub(request, "text-to-audio")

    def audio_to_audio(self, request: AudioToAudioRequest) -> GenerationResult:
        return self._stub(request, "audio-to-audio")

    def inpaint(self, request: InpaintRequest) -> GenerationResult:
        return self._stub(request, "inpainting")

    def continue_audio(self, request: ContinueRequest) -> GenerationResult:
        return self._stub(request, "continuation")

    def _stub(self, request, mode: str) -> GenerationResult:
        job_id = request.job_id or self.storage.new_job(
            mode, request.model_dump(exclude={"job_id"})
        )
        return self.storage.write_error_metadata(
            request=request,
            mode=mode,
            job_id=job_id,
            error=(
                "Stability API provider is a future stub. Configure it after selecting "
                "the relevant Stability API endpoint and terms."
            ),
            provider=self.provider_id,
            model=request.model,
        )
