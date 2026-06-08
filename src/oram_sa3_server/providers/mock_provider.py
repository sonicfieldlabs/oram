from __future__ import annotations

from oram_sa3_server.audio_io import write_silence_wav, write_sine_wav
from oram_sa3_server.providers.base import AudioGenerationProvider
from oram_sa3_server.schemas import (
    AudioToAudioRequest,
    ContinueRequest,
    GenerateRequest,
    GenerationResult,
    InpaintRequest,
)


class MockProvider(AudioGenerationProvider):
    provider_id = "mock"
    sample_rate = 44100

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["mock-sine", "mock-silence", "small-music", "small-sfx", "medium"]

    def load_model(self, model_id: str, device: str = "auto") -> dict:
        self.loaded_model_id = model_id
        self.current_device = "cpu"
        self.last_error = None
        return {
            "provider": self.provider_id,
            "model": model_id,
            "device": self.current_device,
            "status": "loaded",
        }

    def generate(self, request: GenerateRequest) -> GenerationResult:
        return self._render_placeholder(request, "text-to-audio")

    def audio_to_audio(self, request: AudioToAudioRequest) -> GenerationResult:
        self._require_input_audio(request.input_audio_path)
        return self._render_placeholder(request, "audio-to-audio")

    def inpaint(self, request: InpaintRequest) -> GenerationResult:
        self._require_input_audio(request.input_audio_path)
        return self._render_placeholder(request, "inpainting")

    def continue_audio(self, request: ContinueRequest) -> GenerationResult:
        self._require_input_audio(request.input_audio_path)
        render_request = request.model_copy(update={"duration": request.target_duration})
        return self._render_placeholder(render_request, "continuation")

    def load_lora(self, paths: list[str]) -> dict:
        self.loaded_loras = paths
        return {
            "status": "loaded",
            "provider": self.provider_id,
            "loaded_loras": self.loaded_loras,
            "note": "Mock provider records LoRA paths but does not apply them.",
        }

    def set_lora_strength(self, strength: float, lora_index: int | None = None) -> dict:
        return {
            "status": "set",
            "provider": self.provider_id,
            "strength": strength,
            "lora_index": lora_index,
            "loaded_loras": self.loaded_loras,
            "note": "Mock provider records strength requests only.",
        }

    def _render_placeholder(self, request, mode: str) -> GenerationResult:
        job_id = request.job_id or self.storage.new_job(
            mode, request.model_dump(exclude={"job_id"})
        )
        seed = request.seed if request.seed >= 0 else self.storage.random_seed()
        count = request.batch_size if mode == "text-to-audio" else 1
        paths = self.storage.reserve_paths(request=request, mode=mode, job_id=job_id, count=count)
        audio_files: list[str] = []
        metadata_files: list[str] = []

        if not self.loaded_model_id:
            self.load_model(request.model, "cpu")

        for index, (audio_path, metadata_path) in enumerate(paths):
            actual_seed = seed + index
            if request.model == "mock-silence":
                write_silence_wav(
                    audio_path,
                    duration=request.duration,
                    sample_rate=self.sample_rate,
                    channels=2,
                )
            else:
                frequency = 180.0 + ((actual_seed * 37) % 540)
                write_sine_wav(
                    audio_path,
                    duration=request.duration,
                    sample_rate=self.sample_rate,
                    frequency=frequency,
                    amplitude=0.10,
                    channels=2,
                )

            self.storage.write_metadata(
                metadata_path=metadata_path,
                request=request,
                mode=mode,
                provider=self.provider_id,
                model=request.model,
                seed=actual_seed,
                output_audio_path=audio_path,
                sample_rate=self.sample_rate,
                status="done",
                extra={
                    "mock": True,
                    "batch_index": index,
                    "absolute_input_audio_path": self.storage.absolute_path(
                        getattr(request, "input_audio_path", "")
                    )
                    if getattr(request, "input_audio_path", None)
                    else None,
                },
            )
            audio_files.append(self.storage.relative_path(audio_path))
            metadata_files.append(self.storage.relative_path(metadata_path))

        result = GenerationResult(
            job_id=job_id,
            status="done",
            audio_files=audio_files,
            metadata_files=metadata_files,
            seed=seed,
            duration=request.duration,
            sample_rate=self.sample_rate,
            provider=self.provider_id,
            model=request.model,
            mode=mode,
        )
        self.storage.record_result(result)
        return result

    def _require_input_audio(self, path: str) -> None:
        self.storage.resolve_existing_input_audio_path(path, label="input audio")
