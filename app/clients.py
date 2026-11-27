import logging

from openai import AsyncOpenAI

from app.config import settings
from app.model_policy import is_gemini_model

logger = logging.getLogger(__name__)

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def get_client(model: str) -> AsyncOpenAI:
    """Return the appropriate AsyncOpenAI client for the given model."""
    if is_gemini_model(model):
        if not settings.google_api_key:
            raise ValueError(
                "GOOGLE_API_KEY env var is required when using a Gemini model"
            )
        return AsyncOpenAI(
            api_key=settings.google_api_key,
            base_url=GOOGLE_BASE_URL,
            timeout=settings.api_timeout_seconds,
            max_retries=settings.api_max_retries,
        )
    if not settings.openai_api_key:
        raise ValueError(
            "TM_OPENAI_API_KEY env var is required when using an OpenAI model"
        )
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.api_timeout_seconds,
        max_retries=settings.api_max_retries,
    )
