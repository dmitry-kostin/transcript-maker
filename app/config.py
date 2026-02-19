from pathlib import Path

from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    openai_api_key: str
    temp_dir: str = str(PROJECT_ROOT / "tmp")
    results_dir: str = str(PROJECT_ROOT / "results")
    max_chunk_size_mb: float = 24.0
    audio_format: str = "mp3"

    model_config = {"env_prefix": "TM_", "env_file": ".env"}


settings = Settings()
