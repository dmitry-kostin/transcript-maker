import logging

from openai import AsyncOpenAI

from app.config import settings
from app.domain.model_policy import provider_for

logger = logging.getLogger(__name__)


def get_client(model: str) -> AsyncOpenAI:
    """Return an AsyncOpenAI client pointed at the provider that serves ``model``."""
    provider = provider_for(model)
    if not provider.api_key:
        raise ValueError(
            f"{provider.api_key_env} env var is required to use {provider.label} models"
        )
    return AsyncOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        timeout=settings.api_timeout_seconds,
        max_retries=settings.api_max_retries,
    )
