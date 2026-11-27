"""API clients for the model providers (all reached through the OpenAI SDK)."""

import logging

from openai import AsyncOpenAI

from app.config import ProviderSettings, Settings
from app.domain.model_policy import ModelPolicy

logger = logging.getLogger(__name__)


class ProviderNotConfigured(ValueError):
    """The provider that serves the requested model has no API key."""


class ClientFactory:
    def __init__(self, settings: Settings, policy: ModelPolicy) -> None:
        self._settings = settings
        self._policy = policy

    def require_provider(self, model: str) -> ProviderSettings:
        """The configuration of the provider that serves ``model``, or ``ProviderNotConfigured``.

        Cheap, no network — call it before starting work that would only fail at the first API call.
        """
        provider = self._policy.provider_for(model)
        configured = self._settings.provider(provider.id)
        if not configured.api_key:
            raise ProviderNotConfigured(f"{configured.api_key_env} env var is required to use {provider.label} models")
        return configured

    def client_for(self, model: str) -> AsyncOpenAI:
        """An AsyncOpenAI client pointed at the provider that serves ``model``."""
        configured = self.require_provider(model)
        return AsyncOpenAI(
            api_key=configured.api_key,
            base_url=configured.base_url,
            timeout=self._settings.api_timeout_seconds,
            max_retries=self._settings.api_max_retries,
        )
