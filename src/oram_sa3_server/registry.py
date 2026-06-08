from __future__ import annotations

from oram_sa3_server.config import Settings, get_settings
from oram_sa3_server.control import ControlRegistry
from oram_sa3_server.providers.base import AudioGenerationProvider
from oram_sa3_server.providers.mock_provider import MockProvider
from oram_sa3_server.providers.stability_api_provider import StabilityAPIProvider
from oram_sa3_server.providers.stable_audio_mlx_provider import StableAudioMLXProvider
from oram_sa3_server.providers.stable_audio_python_provider import StableAudioPythonProvider
from oram_sa3_server.schemas import ProviderStatus
from oram_sa3_server.job_runner import JobRunner
from oram_sa3_server.storage import StorageManager
from oram_sa3_server.strains import StrainRegistry


class ProviderRegistry:
    def __init__(self, settings: Settings, storage: StorageManager) -> None:
        self.settings = settings
        self.storage = storage
        self.providers: dict[str, AudioGenerationProvider] = {
            "mock": MockProvider(storage),
            "stable_audio_python": StableAudioPythonProvider(storage),
            "stable_audio_mlx": StableAudioMLXProvider(storage, settings),
            "stability_api": StabilityAPIProvider(storage, settings),
        }
        self.active_provider_id = (
            settings.active_provider if settings.active_provider in self.providers else "mock"
        )

    def get(self, provider_id: str | None = None) -> AudioGenerationProvider:
        provider_id = provider_id or self.active_provider_id
        try:
            return self.providers[provider_id]
        except KeyError as exc:
            raise ValueError(f"unknown provider: {provider_id}") from exc

    def set_active(self, provider_id: str) -> None:
        self.get(provider_id)
        self.active_provider_id = provider_id

    def list_status(self) -> list[ProviderStatus]:
        statuses: list[ProviderStatus] = []
        for provider in self.providers.values():
            try:
                available = provider.is_available()
                detail = provider.status_detail()
            except Exception as exc:
                available = False
                detail = str(exc)
            statuses.append(
                ProviderStatus(
                    id=provider.provider_id,
                    available=available,
                    models=provider.list_models(),
                    loaded_model=provider.loaded_model_id,
                    device=provider.current_device,
                    detail=detail,
                )
            )
        return statuses

    def load_model(self, provider_id: str, model_id: str, device: str = "auto") -> dict:
        provider = self.get(provider_id)
        result = provider.load_model(model_id, device)
        self.set_active(provider_id)
        return result

    def loaded_models(self) -> list[str]:
        return [
            f"{provider.provider_id}:{provider.loaded_model_id}"
            for provider in self.providers.values()
            if provider.loaded_model_id
        ]

    def active_device(self) -> str:
        return self.get(self.active_provider_id).current_device


settings = get_settings()
storage = StorageManager(settings)
registry = ProviderRegistry(settings, storage)
job_runner = JobRunner(settings, storage)
control_registry = ControlRegistry(storage)
strain_registry = StrainRegistry(storage)
