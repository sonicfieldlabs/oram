from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from oram_sa3_server.config import Settings
from oram_sa3_server.providers.base import AudioGenerationProvider
from oram_sa3_server.schemas import (
    AudioToAudioRequest,
    ContinueRequest,
    GenerateRequest,
    GenerationResult,
    InpaintRequest,
)


class StableAudioMLXProvider(AudioGenerationProvider):
    provider_id = "stable_audio_mlx"
    sample_rate = 44100

    def __init__(self, storage, settings: Settings) -> None:
        super().__init__(storage)
        self.settings = settings
        self.current_device = (
            "mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "unknown"
        )

    def is_available(self) -> bool:
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            self.last_error = "MLX provider requires Apple Silicon."
            return False
        if not self.sa3_binary().exists():
            self.last_error = (
                "MLX sa3 binary was not found. Run scripts/install_mlx_provider.sh "
                "or set GERMINATOR_MLX_REPO_DIR."
            )
            return False
        self.last_error = None
        return True

    def list_models(self) -> list[str]:
        return ["sm-sfx", "sm-music", "medium", "medium-mlx"]

    def load_model(self, model_id: str, device: str = "auto") -> dict:
        if self._map_model(model_id) not in {"sm-music", "sm-sfx", "medium"}:
            raise ValueError(f"unknown MLX model: {model_id}")
        self.loaded_model_id = model_id
        self.current_device = "mlx"
        return {
            "provider": self.provider_id,
            "model": model_id,
            "device": self.current_device,
            "status": "ready",
            "detail": "MLX loads weights in the sa3 subprocess.",
        }

    def generate(self, request: GenerateRequest) -> GenerationResult:
        return self._run_sa3(request, "text-to-audio")

    def audio_to_audio(self, request: AudioToAudioRequest) -> GenerationResult:
        return self._run_sa3(request, "audio-to-audio")

    def inpaint(self, request: InpaintRequest) -> GenerationResult:
        return self._run_sa3(request, "inpainting")

    def continue_audio(self, request: ContinueRequest) -> GenerationResult:
        inpaint_request = InpaintRequest(
            **request.model_dump(
                exclude={"source_duration", "target_duration", "duration", "job_id"}
            ),
            inpaint_ranges=[(request.source_duration, request.target_duration)],
            duration=request.target_duration,
            job_id=request.job_id,
        )
        return self._run_sa3(inpaint_request, "continuation")

    def _run_sa3(self, request, mode: str) -> GenerationResult:
        job_id = request.job_id or self.storage.new_job(
            mode, request.model_dump(exclude={"job_id"})
        )
        count = request.batch_size if mode == "text-to-audio" else 1
        paths = self.storage.reserve_paths(request=request, mode=mode, job_id=job_id, count=count)
        first_seed = request.seed if request.seed >= 0 else self.storage.random_seed()

        if not self.is_available():
            return self.storage.write_error_metadata(
                request=request,
                mode=mode,
                job_id=job_id,
                error=self.last_error or "MLX provider is not available",
                provider=self.provider_id,
                model=request.model,
            )

        if isinstance(request, InpaintRequest) and len(request.inpaint_ranges) > 1:
            return self._run_multi_range_inpaint(
                request=request,
                mode=mode,
                job_id=job_id,
                paths=paths[0],
                first_seed=first_seed,
            )

        audio_files: list[str] = []
        metadata_files: list[str] = []
        for index, (audio_path, metadata_path) in enumerate(paths):
            seed = (
                first_seed + index
                if request.seed >= 0
                else (first_seed if index == 0 else self.storage.random_seed())
            )
            try:
                command = self._build_command(request, audio_path, seed)
            except Exception as exc:
                return self.storage.write_error_metadata(
                    request=request,
                    mode=mode,
                    job_id=job_id,
                    error=str(exc),
                    provider=self.provider_id,
                    model=request.model,
                )

            command_text = " ".join(command)
            try:
                process = subprocess.run(
                    command,
                    cwd=str(self.mlx_dir()),
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.settings.provider_timeout_seconds,
                )
                stdout = process.stdout
                stderr = process.stderr
                returncode = process.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout or ""
                stderr = exc.stderr or ""
                returncode = -1
                error = (
                    f"MLX sa3 timed out after "
                    f"{self.settings.provider_timeout_seconds:.0f} seconds."
                )
                self.storage.write_metadata(
                    metadata_path=metadata_path,
                    request=request,
                    mode=mode,
                    provider=self.provider_id,
                    model=request.model,
                    seed=seed,
                    output_audio_path=audio_path if audio_path.exists() else None,
                    sample_rate=self.sample_rate if audio_path.exists() else None,
                    status="error",
                    error=error,
                    extra={
                        "batch_index": index,
                        "command": command_text,
                        "stdout": stdout,
                        "stderr": stderr,
                        "returncode": returncode,
                    },
                )
                result = GenerationResult(
                    job_id=job_id,
                    status="error",
                    audio_files=audio_files,
                    metadata_files=metadata_files + [self.storage.relative_path(metadata_path)],
                    seed=first_seed,
                    duration=request.duration,
                    sample_rate=self.sample_rate,
                    error=error,
                    provider=self.provider_id,
                    model=request.model,
                    mode=mode,
                )
                self.storage.record_result(result)
                return result

            if returncode != 0:
                error = (
                    f"MLX sa3 exited with code {returncode}. "
                    f"stderr: {stderr.strip() or 'empty'}"
                )
                self.storage.write_metadata(
                    metadata_path=metadata_path,
                    request=request,
                    mode=mode,
                    provider=self.provider_id,
                    model=request.model,
                    seed=seed,
                    output_audio_path=audio_path if audio_path.exists() else None,
                    sample_rate=self.sample_rate if audio_path.exists() else None,
                    status="error",
                    error=error,
                    extra={
                        "batch_index": index,
                        "command": command_text,
                        "stdout": stdout,
                        "stderr": stderr,
                        "returncode": returncode,
                    },
                )
                result = GenerationResult(
                    job_id=job_id,
                    status="error",
                    audio_files=audio_files,
                    metadata_files=metadata_files + [self.storage.relative_path(metadata_path)],
                    seed=first_seed,
                    duration=request.duration,
                    sample_rate=self.sample_rate,
                    error=error,
                    provider=self.provider_id,
                    model=request.model,
                    mode=mode,
                )
                self.storage.record_result(result)
                return result

            self.storage.write_metadata(
                metadata_path=metadata_path,
                request=request,
                mode=mode,
                provider=self.provider_id,
                model=request.model,
                seed=seed,
                output_audio_path=audio_path,
                sample_rate=self.sample_rate,
                status="done",
                extra={
                    "batch_index": index,
                    "command": command_text,
                    "stdout": stdout,
                    "stderr": stderr,
                },
            )
            audio_files.append(self.storage.relative_path(audio_path))
            metadata_files.append(self.storage.relative_path(metadata_path))

        result = GenerationResult(
            job_id=job_id,
            status="done",
            audio_files=audio_files,
            metadata_files=metadata_files,
            seed=first_seed,
            duration=request.duration,
            sample_rate=self.sample_rate,
            provider=self.provider_id,
            model=request.model,
            mode=mode,
        )
        self.storage.record_result(result)
        return result

    def _build_command(self, request, audio_path: Path, seed: int) -> list[str]:
        if self._map_model(request.model) not in {"sm-music", "sm-sfx", "medium"}:
            raise ValueError(f"unknown MLX model: {request.model}")
        if isinstance(request, InpaintRequest) and len(request.inpaint_ranges) > 1:
            raise ValueError(
                "Build one MLX inpaint command per range, then run them sequentially."
            )

        command = [
            str(self.sa3_binary()),
            "--prompt",
            request.prompt,
            "--dit",
            self._map_model(request.model),
            "--decoder",
            self._decoder_for_model(request.model),
            "--seconds",
            str(request.duration),
            "--out",
            str(audio_path.resolve()),
            "--cfg",
            str(request.cfg_scale),
            "--steps",
            str(request.steps),
            "--seed",
            str(seed),
        ]
        if request.negative_prompt:
            command.extend(["--negative-prompt", request.negative_prompt])
        if isinstance(request, AudioToAudioRequest):
            input_path = self._resolve_input(request.input_audio_path)
            command.extend(["--init-audio", input_path])
            command.extend(["--init-noise-level", str(request.init_noise_level)])
        if isinstance(request, InpaintRequest):
            input_path = self._resolve_input(request.input_audio_path)
            start, end = request.inpaint_ranges[0]
            command.extend(["--init-audio", input_path])
            command.extend(["--inpaint-range", f"{start},{end}"])
        return command

    def _run_multi_range_inpaint(
        self,
        *,
        request: InpaintRequest,
        mode: str,
        job_id: str,
        paths: tuple[Path, Path],
        first_seed: int,
    ) -> GenerationResult:
        audio_path, metadata_path = paths
        scratch_dir = self.settings.output_root / "intermediate"
        scratch_dir.mkdir(parents=True, exist_ok=True)

        current_source = self._resolve_input(request.input_audio_path)
        commands: list[str] = []
        stdouts: list[str] = []
        stderrs: list[str] = []
        returncodes: list[int] = []
        range_seeds: list[int] = []
        intermediate_files: list[str] = []
        intermediate_paths: list[Path] = []

        for index, range_pair in enumerate(request.inpaint_ranges):
            seed = (
                first_seed + index
                if request.seed >= 0
                else (first_seed if index == 0 else self.storage.random_seed())
            )
            range_seeds.append(seed)
            is_last_range = index == len(request.inpaint_ranges) - 1
            target_path = (
                audio_path
                if is_last_range
                else scratch_dir / f"{audio_path.stem}_range_{index + 1:02d}.wav"
            )
            single_request = request.model_copy(
                update={
                    "input_audio_path": current_source,
                    "inpaint_ranges": [range_pair],
                }
            )
            try:
                command = self._build_command(single_request, target_path, seed)
            except Exception as exc:
                self._cleanup_paths(intermediate_paths)
                return self.storage.write_error_metadata(
                    request=request,
                    mode=mode,
                    job_id=job_id,
                    error=str(exc),
                    provider=self.provider_id,
                    model=request.model,
                )

            process = self._run_process(command)
            commands.append(process["command"])
            stdouts.append(process["stdout"])
            stderrs.append(process["stderr"])
            returncodes.append(process["returncode"])

            error = process["error"]
            if error is None and process["returncode"] != 0:
                error = (
                    f"MLX sa3 exited with code {process['returncode']} on range "
                    f"{index + 1}/{len(request.inpaint_ranges)}. "
                    f"stderr: {process['stderr'].strip() or 'empty'}"
                )
            if error is not None:
                self._cleanup_paths(intermediate_paths)
                self.storage.write_metadata(
                    metadata_path=metadata_path,
                    request=request,
                    mode=mode,
                    provider=self.provider_id,
                    model=request.model,
                    seed=first_seed,
                    output_audio_path=target_path if target_path.exists() else None,
                    sample_rate=self.sample_rate if target_path.exists() else None,
                    status="error",
                    error=error,
                    extra={
                        "multi_range_strategy": "sequential_mlx_inpaint",
                        "failed_range_index": index,
                        "commands": commands,
                        "stdout": stdouts,
                        "stderr": stderrs,
                        "returncodes": returncodes,
                        "range_seeds": range_seeds,
                        "intermediate_files": intermediate_files,
                    },
                )
                result = GenerationResult(
                    job_id=job_id,
                    status="error",
                    audio_files=[],
                    metadata_files=[self.storage.relative_path(metadata_path)],
                    seed=first_seed,
                    duration=request.duration,
                    sample_rate=self.sample_rate,
                    error=error,
                    provider=self.provider_id,
                    model=request.model,
                    mode=mode,
                )
                self.storage.record_result(result)
                return result

            if not is_last_range:
                intermediate_files.append(self.storage.relative_path(target_path))
                intermediate_paths.append(target_path)
                current_source = str(target_path.resolve())

        # The run succeeded; only the final output is needed. Remove the
        # per-range scratch files so output/intermediate does not grow unbounded.
        self._cleanup_paths(intermediate_paths)

        self.storage.write_metadata(
            metadata_path=metadata_path,
            request=request,
            mode=mode,
            provider=self.provider_id,
            model=request.model,
            seed=first_seed,
            output_audio_path=audio_path,
            sample_rate=self.sample_rate,
            status="done",
            extra={
                "multi_range_strategy": "sequential_mlx_inpaint",
                "commands": commands,
                "stdout": stdouts,
                "stderr": stderrs,
                "returncodes": returncodes,
                "range_seeds": range_seeds,
                "intermediate_files": intermediate_files,
                "intermediate_files_cleaned": True,
            },
        )
        result = GenerationResult(
            job_id=job_id,
            status="done",
            audio_files=[self.storage.relative_path(audio_path)],
            metadata_files=[self.storage.relative_path(metadata_path)],
            seed=first_seed,
            duration=request.duration,
            sample_rate=self.sample_rate,
            provider=self.provider_id,
            model=request.model,
            mode=mode,
        )
        self.storage.record_result(result)
        return result

    def _run_process(self, command: list[str]) -> dict:
        command_text = " ".join(command)
        try:
            process = subprocess.run(
                command,
                cwd=str(self.mlx_dir()),
                text=True,
                capture_output=True,
                check=False,
                timeout=self.settings.provider_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "command": command_text,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "returncode": -1,
                "error": (
                    f"MLX sa3 timed out after "
                    f"{self.settings.provider_timeout_seconds:.0f} seconds."
                ),
            }
        return {
            "command": command_text,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "returncode": process.returncode,
            "error": None,
        }

    @staticmethod
    def _cleanup_paths(paths: list[Path]) -> None:
        for scratch in paths:
            try:
                scratch.unlink(missing_ok=True)
            except OSError:
                pass

    def mlx_dir(self) -> Path:
        root = self.settings.mlx_repo_dir
        if (root / "sa3").exists() and root.name == "mlx":
            return root
        if (root / "optimized" / "mlx" / "sa3").exists():
            return root / "optimized" / "mlx"
        return root / "optimized" / "mlx"

    def sa3_binary(self) -> Path:
        return self.mlx_dir() / "sa3"

    @staticmethod
    def _map_model(model_id: str) -> str:
        return "medium" if model_id == "medium-mlx" else model_id

    def _decoder_for_model(self, model_id: str) -> str:
        if self._map_model(model_id) == "medium" and self.settings.mlx_decoder == "same-s":
            return "same-l"
        return self.settings.mlx_decoder

    def _resolve_input(self, path: str) -> str:
        return str(self.storage.resolve_existing_input_audio_path(path, label="input audio"))
