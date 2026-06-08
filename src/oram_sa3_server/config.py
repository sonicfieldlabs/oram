from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from oram_sa3_server.identity import PRODUCT_NAME


PROJECT_ROOT = Path(__file__).resolve().parents[0]
load_dotenv(PROJECT_ROOT / ".env")
DEFAULT_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "testserver"}


def _path_from_env(name: str, default: str) -> Path:
    value = os.getenv(name, default)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _path_list_from_env(name: str, defaults: list[Path]) -> list[Path]:
    raw = os.getenv(name)
    if not raw:
        return [path.resolve() for path in defaults]
    paths: list[Path] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        paths.append(path.resolve())
    return paths or [path.resolve() for path in defaults]


class Settings:
    server_name = PRODUCT_NAME
    engine_name = "stable-audio-3"

    def __init__(self) -> None:
        self.project_root = PROJECT_ROOT
        self.host = os.getenv("GERMINATOR_HOST", "127.0.0.1")
        self.port = int(os.getenv("GERMINATOR_PORT", "8765"))
        self.active_provider = os.getenv("GERMINATOR_ACTIVE_PROVIDER", "mock")
        self.default_model = os.getenv("GERMINATOR_DEFAULT_MODEL", "small-sfx")
        self.default_device = os.getenv("GERMINATOR_DEFAULT_DEVICE", "auto")
        self.output_root = _path_from_env("GERMINATOR_OUTPUT_DIR", "output")
        self.audio_dir = self.output_root / "audio"
        self.metadata_dir = self.output_root / "metadata"
        self.upload_dir = self.output_root / "uploads"
        self.scratch_dir = self.output_root / "scratch"
        self.allowed_input_roots = _path_list_from_env(
            "GERMINATOR_ALLOWED_INPUT_ROOTS",
            [self.output_root],
        )
        self.official_repo_dir = _path_from_env(
            "GERMINATOR_OFFICIAL_REPO_DIR", "vendor/stable-audio-3"
        )
        self.mlx_repo_dir = _path_from_env("GERMINATOR_MLX_REPO_DIR", "vendor/stable-audio-3")
        self.allowed_model_roots = _path_list_from_env(
            "GERMINATOR_ALLOWED_MODEL_ROOTS",
            [self.official_repo_dir, self.mlx_repo_dir, self.output_root],
        )
        self.mlx_decoder = os.getenv("GERMINATOR_MLX_DECODER", "same-s")
        self.provider_timeout_seconds = float(
            os.getenv("GERMINATOR_PROVIDER_TIMEOUT_SECONDS", "1800")
        )
        self.job_workers = max(1, int(os.getenv("GERMINATOR_JOB_WORKERS", "1")))
        self.stability_api_key = os.getenv("STABILITY_API_KEY", "")
        self.allowed_hosts = self._parse_allowed_hosts()
        self.max_upload_bytes = int(
            float(os.getenv("GERMINATOR_MAX_UPLOAD_MB", "100")) * 1024 * 1024
        )
        self.max_image_upload_bytes = int(
            float(os.getenv("GERMINATOR_MAX_IMAGE_MB", "8")) * 1024 * 1024
        )

    def _parse_allowed_hosts(self) -> list[str]:
        raw = os.getenv("GERMINATOR_ALLOWED_HOSTS")
        if raw is None:
            hosts = set(DEFAULT_ALLOWED_HOSTS)
            if self.host and self.host not in {"0.0.0.0", "::"}:
                hosts.add(self.host)
            return sorted(hosts)
        entries = [item.strip() for item in raw.split(",") if item.strip()]
        if not entries:
            hosts = set(DEFAULT_ALLOWED_HOSTS)
            if self.host and self.host not in {"0.0.0.0", "::"}:
                hosts.add(self.host)
            return sorted(hosts)
        if "*" in entries:
            return ["*"]
        hosts = set(entries)
        hosts.add("testserver")
        return sorted(hosts)


@lru_cache
def get_settings() -> Settings:
    return Settings()
