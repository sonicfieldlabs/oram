from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from typing import Any

from oram_sa3_server.config import Settings
from oram_sa3_server.registry import ProviderRegistry


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def provider_install_commands(settings: Settings) -> dict[str, list[str]]:
    return {
        "mock": ["No install needed."],
        "stable_audio_mlx": [
            "cd " + str(settings.project_root),
            "./scripts/install_mlx_provider.sh",
            "./launch_germinator.command",
        ],
        "stable_audio_python": [
            "cd " + str(settings.project_root),
            "./scripts/install_python_provider.sh",
            "./launch_germinator.command",
        ],
        "stability_api": [
            "Future provider stub. Local testing does not need STABILITY_API_KEY.",
        ],
    }


def recommended_local_route() -> str:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "stable_audio_mlx"
    return "stable_audio_python"


def environment_report(settings: Settings, registry: ProviderRegistry) -> dict[str, Any]:
    official_repo = settings.official_repo_dir
    mlx_dir = settings.mlx_repo_dir
    if (mlx_dir / "optimized" / "mlx").exists():
        mlx_cli_dir = mlx_dir / "optimized" / "mlx"
    else:
        mlx_cli_dir = mlx_dir

    providers = [status.model_dump() for status in registry.list_status()]
    deps = {
        "fastapi": module_available("fastapi"),
        "stable_audio_3": module_available("stable_audio_3"),
        "torch": module_available("torch"),
        "torchaudio": module_available("torchaudio"),
    }
    huggingface = {
        "hf_cli": shutil.which("hf"),
        "huggingface_cli": shutil.which("huggingface-cli"),
        "hf_token_env_present": bool(os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")),
        "note": (
            "Python provider model weights may require accepting Stability AI terms "
            "on Hugging Face and logging in with a read token. MLX optimized weights "
            "can be tested through stable_audio_mlx."
        ),
    }
    rubberband_path = shutil.which("rubberband-r3") or shutil.which("rubberband")
    audio_processing = {
        "rubberband_cli": rubberband_path,
        "rubberband_available": bool(rubberband_path),
        "time_pitch_available": bool(rubberband_path),
        "note": "Rubber Band enables offline time-stretch and pitch-shift renders.",
    }
    paths = {
        "official_repo_dir": str(official_repo),
        "official_repo_exists": official_repo.exists(),
        "official_run_gradio_exists": (official_repo / "run_gradio.py").exists(),
        "mlx_repo_dir": str(settings.mlx_repo_dir),
        "mlx_cli_dir": str(mlx_cli_dir),
        "mlx_sa3_exists": (mlx_cli_dir / "sa3").exists(),
        "output_audio_dir": str(settings.audio_dir),
        "output_metadata_dir": str(settings.metadata_dir),
    }

    missing: list[str] = []
    if not deps["stable_audio_3"]:
        missing.append("stable_audio_3 Python package")
    if not deps["torch"]:
        missing.append("torch")
    if not deps["torchaudio"]:
        missing.append("torchaudio")
    if (
        platform.system() == "Darwin"
        and platform.machine() == "arm64"
        and not paths["mlx_sa3_exists"]
    ):
        missing.append("Stable Audio 3 MLX CLI at optimized/mlx/sa3")

    route = recommended_local_route()
    workflow = [
        "1. Use mock first and run a 1-second test render.",
        f"2. Install {route}.",
        "3. Restart the launcher.",
        "4. Open /dashboard, select provider/model, click Load, then Run Test.",
        "5. A working local model returns status=done and writes WAV plus metadata.",
    ]

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "is_apple_silicon": platform.system() == "Darwin" and platform.machine() == "arm64",
        "uv": shutil.which("uv"),
        "active_provider": registry.active_provider_id,
        "recommended_local_provider": route,
        "dependencies": deps,
        "audio_processing": audio_processing,
        "huggingface": huggingface,
        "paths": paths,
        "providers": providers,
        "missing_for_real_local_models": missing,
        "install_commands": provider_install_commands(settings),
        "test_workflow": workflow,
    }
