from __future__ import annotations

import importlib.util
from typing import Any

from oram_sa3_server.providers.base import AudioGenerationProvider
from oram_sa3_server.schemas import (
    AudioToAudioRequest,
    ContinueRequest,
    GenerateRequest,
    GenerationResult,
    InpaintRequest,
)


class StableAudioPythonProvider(AudioGenerationProvider):
    provider_id = "stable_audio_python"
    sample_rate = 44100

    def __init__(self, storage) -> None:
        super().__init__(storage)
        self.model: Any | None = None

    def is_available(self) -> bool:
        available = importlib.util.find_spec("stable_audio_3") is not None
        if not available:
            self.last_error = (
                "stable_audio_3 is not importable. Run scripts/install_python_provider.sh."
            )
        else:
            self.last_error = None
        return available

    def list_models(self) -> list[str]:
        return [
            "small-music",
            "small-sfx",
            "medium",
            "small-music-base",
            "small-sfx-base",
            "medium-base",
        ]

    def load_model(self, model_id: str, device: str = "auto") -> dict:
        if model_id not in self.list_models():
            raise ValueError(f"unknown Stable Audio Python model: {model_id}")
        resolved_device = self._resolve_device(device)
        if (
            self.model is not None
            and self.loaded_model_id == model_id
            and self.current_device == resolved_device
        ):
            return {
                "provider": self.provider_id,
                "model": self.loaded_model_id,
                "device": self.current_device,
                "status": "loaded",
                "detail": "model already loaded",
            }
        if not self.is_available():
            raise RuntimeError(self.last_error)

        from stable_audio_3 import StableAudioModel

        self.model = StableAudioModel.from_pretrained(model_id, device=resolved_device)
        self.loaded_model_id = model_id
        self.current_device = resolved_device
        self.last_error = None
        return {
            "provider": self.provider_id,
            "model": self.loaded_model_id,
            "device": self.current_device,
            "status": "loaded",
        }

    def generate(self, request: GenerateRequest) -> GenerationResult:
        return self._generate_with_model(request, "text-to-audio")

    def audio_to_audio(self, request: AudioToAudioRequest) -> GenerationResult:
        return self._generate_with_model(request, "audio-to-audio")

    def inpaint(self, request: InpaintRequest) -> GenerationResult:
        return self._generate_with_model(request, "inpainting")

    def continue_audio(self, request: ContinueRequest) -> GenerationResult:
        ranges = [(request.source_duration, request.target_duration)]
        inpaint_request = InpaintRequest(
            **request.model_dump(
                exclude={"source_duration", "target_duration", "duration", "job_id"}
            ),
            inpaint_ranges=ranges,
            duration=request.target_duration,
            job_id=request.job_id,
        )
        return self._generate_with_model(inpaint_request, "continuation")

    def load_lora(self, paths: list[str]) -> dict:
        if self.model is None:
            raise RuntimeError("load a Stable Audio model before loading LoRAs")
        resolved_paths = [
            str(self.storage.resolve_existing_model_file_path(path, label="LoRA checkpoint"))
            for path in paths
        ]
        if not hasattr(self.model, "load_lora"):
            raise RuntimeError("Installed stable_audio_3 model does not expose load_lora().")

        loaded = self.model.load_lora(resolved_paths)
        for path in resolved_paths:
            if path not in self.loaded_loras:
                self.loaded_loras.append(path)
        return {
            "status": "loaded",
            "provider": self.provider_id,
            "loaded_loras": self.loaded_loras,
            "detail": str(loaded) if loaded is not None else None,
        }

    def set_lora_strength(self, strength: float, lora_index: int | None = None) -> dict:
        if self.model is None:
            raise RuntimeError("load a Stable Audio model before setting LoRA strength")
        if not hasattr(self.model, "set_lora_strength"):
            raise RuntimeError(
                "Installed stable_audio_3 model does not expose set_lora_strength()."
            )

        self.model.set_lora_strength(strength, lora_index=lora_index)
        return {
            "status": "set",
            "provider": self.provider_id,
            "strength": strength,
            "lora_index": lora_index,
            "loaded_loras": self.loaded_loras,
        }

    def _generate_with_model(self, request, mode: str) -> GenerationResult:
        if self.model is None or self.loaded_model_id != request.model:
            device = self.current_device if self.current_device != "unknown" else "auto"
            self.load_model(request.model, device)

        for lora in request.lora:
            resolved_lora = str(
                self.storage.resolve_existing_model_file_path(lora.path, label="LoRA checkpoint")
            )
            if resolved_lora not in self.loaded_loras:
                self.load_lora([resolved_lora])
            if lora.strength is not None:
                self.set_lora_strength(lora.strength)

        import torchaudio

        job_id = request.job_id or self.storage.new_job(
            mode, request.model_dump(exclude={"job_id"})
        )
        seed = request.seed if request.seed >= 0 else self.storage.random_seed()
        count = request.batch_size if mode == "text-to-audio" else 1
        paths = self.storage.reserve_paths(request=request, mode=mode, job_id=job_id, count=count)

        kwargs = {
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "duration": request.duration,
            "steps": request.steps,
            "cfg_scale": request.cfg_scale,
            "seed": seed,
            "batch_size": count,
            "chunked_decode": request.chunked_decode,
        }
        if isinstance(request, AudioToAudioRequest):
            kwargs["init_audio"] = self._load_stable_audio_input(request.input_audio_path)
            kwargs["init_noise_level"] = request.init_noise_level
        if isinstance(request, InpaintRequest):
            starts = [start for start, _ in request.inpaint_ranges]
            ends = [end for _, end in request.inpaint_ranges]
            kwargs["inpaint_audio"] = self._load_stable_audio_input(request.input_audio_path)
            kwargs["inpaint_mask_start_seconds"] = starts
            kwargs["inpaint_mask_end_seconds"] = ends

        audio = self.model.generate(**kwargs)
        audio_files: list[str] = []
        metadata_files: list[str] = []

        sample_rate = self._model_sample_rate()
        for index, (audio_path, metadata_path) in enumerate(paths):
            waveform = self._select_batch(audio, index)
            torchaudio.save(str(audio_path), waveform, sample_rate)
            self.storage.write_metadata(
                metadata_path=metadata_path,
                request=request,
                mode=mode,
                provider=self.provider_id,
                model=request.model,
                seed=seed + index if request.seed >= 0 else seed,
                output_audio_path=audio_path,
                sample_rate=sample_rate,
                status="done",
                extra={"batch_index": index, "device": self.current_device},
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
            sample_rate=sample_rate,
            provider=self.provider_id,
            model=request.model,
            mode=mode,
        )
        self.storage.record_result(result)
        return result

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device != "auto":
            return device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return "mps"
        except Exception:
            pass
        return "cpu"

    def _model_sample_rate(self) -> int:
        model = getattr(self.model, "model", None)
        return int(getattr(model, "sample_rate", self.sample_rate))

    def _load_stable_audio_input(self, path: str):
        import torchaudio

        expanded = self.storage.resolve_existing_input_audio_path(path, label="input audio")
        waveform, sample_rate = torchaudio.load(str(expanded))
        return (sample_rate, waveform)

    @staticmethod
    def _select_batch(audio, index: int):
        import torch

        if isinstance(audio, (list, tuple)):
            audio = audio[index]
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu()
        if not isinstance(audio, torch.Tensor):
            audio = torch.as_tensor(audio)
        if audio.ndim == 3:
            audio = audio[index]
        if audio.ndim == 1:
            audio = audio.unsqueeze(0)
        if audio.shape[0] > 2:
            audio = audio[:2]
        if audio.shape[0] == 1:
            audio = audio.repeat(2, 1)
        return audio.float()
