import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def is_gemini_model(model: str) -> bool:
    """Check if a model name is a Gemini model."""
    return model.startswith("gemini-")


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
        )
    if not settings.openai_api_key:
        raise ValueError(
            "TM_OPENAI_API_KEY env var is required when using an OpenAI model"
        )
    return AsyncOpenAI(api_key=settings.openai_api_key)
