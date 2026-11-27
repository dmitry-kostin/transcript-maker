"""Which model providers can be used here, and with which models."""

from app.config import Settings
from app.domain.model_policy import ModelPolicy, Provider


class Providers:
    def __init__(self, settings: Settings, policy: ModelPolicy) -> None:
        self._settings = settings
        self._policy = policy

    def available(self) -> list[dict]:
        """Providers that have an API key configured, with the models each one would use."""
        return [
            self._describe(provider)
            for provider in self._policy.providers
            if self._settings.provider(provider.id).api_key
        ]

    def _describe(self, provider: Provider) -> dict:
        settings, own = self._settings, self._settings.provider(provider.id)
        return {
            "id": provider.id,
            "label": provider.label,
            "transcribe_model": self._policy.default_model_for(
                provider, settings.transcribe_model, own.transcribe_model
            ),
            "summarize_model": self._policy.default_model_for(provider, settings.summarize_model, own.summarize_model),
        }
