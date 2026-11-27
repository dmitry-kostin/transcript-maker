"""Model naming and provider rules.

Pure policy: no network clients, no file access. Everything that decides *which*
model a request means lives here, so the HTTP layer, the pipeline and the API
clients all agree on one answer.
"""
from app.config import settings

LEGACY_DIARIZE_SUFFIX = "-diarize"


def is_gemini_model(model: str) -> bool:
    """Check if a model name is a Gemini model."""
    return model.startswith("gemini-")


def resolve_model(requested: str) -> str:
    """Resolve a requested model string into the model that will actually run.

    - "" → settings.transcribe_model
    - explicit model name → that model
    - a legacy "<model>-diarize" name (speaker detection was removed; old records
      still carry these names) → the base model
    """
    model = requested.removesuffix(LEGACY_DIARIZE_SUFFIX)
    return model or settings.transcribe_model


def available_providers() -> list[dict]:
    """Providers that have an API key configured, with the models each one would use.

    The global default model wins for the provider it belongs to; the other
    provider falls back to its own per-provider default.
    """
    providers = []
    if settings.openai_api_key:
        providers.append({
            "id": "openai",
            "label": "OpenAI",
            "transcribe_model": settings.openai_transcribe_model if is_gemini_model(settings.transcribe_model) else settings.transcribe_model,
            "summarize_model": settings.openai_summarize_model if is_gemini_model(settings.summarize_model) else settings.summarize_model,
        })
    if settings.google_api_key:
        providers.append({
            "id": "gemini",
            "label": "Gemini",
            "transcribe_model": settings.transcribe_model if is_gemini_model(settings.transcribe_model) else settings.gemini_transcribe_model,
            "summarize_model": settings.summarize_model if is_gemini_model(settings.summarize_model) else settings.gemini_summarize_model,
        })
    return providers
