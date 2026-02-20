from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    openai_api_key: str = ""
    google_api_key: str = Field(default="", validation_alias="GOOGLE_API_KEY")
    temp_dir: str = str(PROJECT_ROOT / "tmp")
    results_dir: str = str(PROJECT_ROOT / "results")
    max_chunk_size_mb: float = 24.0
    audio_format: str = "mp3"
    summarize_model: str = "gpt-4o"
    transcribe_model: str = "gpt-4o-transcribe"

    # Per-provider model defaults (used by provider selector when switching providers)
    openai_transcribe_model: str = "gpt-4o-transcribe"
    openai_summarize_model: str = "gpt-4o"
    gemini_transcribe_model: str = "gemini-3-flash-preview"
    gemini_summarize_model: str = "gemini-3-flash-preview"

    model_config = {"env_prefix": "TM_", "env_file": ".env"}


settings = Settings()
