from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).parent.parent


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass(frozen=True)
class ProviderSettings:
    """How one model provider is configured here: its key, endpoint and default models."""

    api_key: str
    api_key_env: str  # shown in the "key is missing" error
    base_url: str | None  # None → the SDK default (OpenAI)
    transcribe_model: str
    summarize_model: str


class Settings(BaseSettings):
    openai_api_key: str = ""
    google_api_key: str = Field(default="", validation_alias="GOOGLE_API_KEY")
    temp_dir: str = str(PROJECT_ROOT / "tmp")
    results_dir: str = str(PROJECT_ROOT / "results")
    log_file: str = str(PROJECT_ROOT / "logs" / "app.log")
    max_chunk_size_mb: float = 24.0
    audio_format: str = "mp3"
    summarize_model: str = "gpt-6-luna"
    transcribe_model: str = "gpt-transcribe"

    # Per-provider model defaults (used by provider selector when switching providers)
    openai_transcribe_model: str = "gpt-transcribe"
    openai_summarize_model: str = "gpt-6-luna"
    gemini_transcribe_model: str = "gemini-3.5-transcribe"
    gemini_summarize_model: str = "gemini-3.8-flash"

    # One API request may take this long before it is abandoned (the SDK default is 10 minutes)
    api_timeout_seconds: float = 180.0
    api_max_retries: int = 1

    model_config = {"env_prefix": "TM_", "env_file": ".env"}

    def provider(self, provider_id: str) -> ProviderSettings:
        """The configuration of one provider from ``app.domain.model_policy.PROVIDERS``, by its id."""
        if provider_id == "openai":
            return ProviderSettings(
                api_key=self.openai_api_key,
                api_key_env="TM_OPENAI_API_KEY",
                base_url=None,
                transcribe_model=self.openai_transcribe_model,
                summarize_model=self.openai_summarize_model,
            )
        if provider_id == "gemini":
            return ProviderSettings(
                api_key=self.google_api_key,
                api_key_env="GOOGLE_API_KEY",
                base_url=GEMINI_BASE_URL,
                transcribe_model=self.gemini_transcribe_model,
                summarize_model=self.gemini_summarize_model,
            )
        raise KeyError(f"No settings for provider {provider_id!r}")


settings = Settings()
