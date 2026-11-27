"""Model naming and provider rules.

Pure policy: no network clients, no file access. Everything that decides *which*
model a request means, and what its provider can do, lives here — the HTTP layer,
the use cases and the API clients all read the same table.
"""
from dataclasses import dataclass

from app.config import settings

LEGACY_DIARIZE_SUFFIX = "-diarize"

# How a provider takes audio: a dedicated speech-to-text route, or chat with an audio part
TRANSCRIBE_VIA_AUDIO_API = "audio_api"
TRANSCRIBE_VIA_CHAT = "chat"


@dataclass(frozen=True)
class Provider:
    """One model vendor. Adding a vendor means adding one entry to ``PROVIDERS``."""

    id: str
    label: str
    model_prefix: str  # "" matches any model no other provider claims
    api_key_setting: str  # attribute name on ``settings``
    api_key_env: str  # shown in the "key is missing" error
    base_url: str | None  # None → the SDK default (OpenAI)
    transcribe_model_setting: str
    summarize_model_setting: str
    transcribe_via: str
    summary_temperature: float | None  # None → don't send one (GPT-6 rejects non-default values)

    @property
    def api_key(self) -> str:
        return getattr(settings, self.api_key_setting)

    @property
    def default_transcribe_model(self) -> str:
        return getattr(settings, self.transcribe_model_setting)

    @property
    def default_summarize_model(self) -> str:
        return getattr(settings, self.summarize_model_setting)


OPENAI = Provider(
    id="openai",
    label="OpenAI",
    model_prefix="",
    api_key_setting="openai_api_key",
    api_key_env="TM_OPENAI_API_KEY",
    base_url=None,
    transcribe_model_setting="openai_transcribe_model",
    summarize_model_setting="openai_summarize_model",
    transcribe_via=TRANSCRIBE_VIA_AUDIO_API,
    summary_temperature=None,
)

GEMINI = Provider(
    id="gemini",
    label="Gemini",
    model_prefix="gemini-",
    api_key_setting="google_api_key",
    api_key_env="GOOGLE_API_KEY",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    transcribe_model_setting="gemini_transcribe_model",
    summarize_model_setting="gemini_summarize_model",
    transcribe_via=TRANSCRIBE_VIA_CHAT,
    summary_temperature=0.3,
)

# Order is the order the frontend shows them in
PROVIDERS: tuple[Provider, ...] = (OPENAI, GEMINI)


def provider_for(model: str) -> Provider:
    """The provider that serves ``model``: the one whose prefix matches, else the catch-all."""
    for provider in PROVIDERS:
        if provider.model_prefix and model.startswith(provider.model_prefix):
            return provider
    return next(p for p in PROVIDERS if not p.model_prefix)


def resolve_model(requested: str) -> str:
    """Resolve a requested transcription model into the model that will actually run.

    - "" → settings.transcribe_model
    - explicit model name → that model
    - a legacy "<model>-diarize" name (speaker detection was removed; old records
      still carry these names) → the base model
    """
    model = requested.removesuffix(LEGACY_DIARIZE_SUFFIX)
    return model or settings.transcribe_model


def resolve_summarize_model(requested: str) -> str:
    """Resolve a requested summarization model: "" → settings.summarize_model."""
    return requested or settings.summarize_model


def _model_for(provider: Provider, global_default: str, provider_default: str) -> str:
    """The global default wins for the provider it belongs to; others use their own default."""
    return global_default if provider_for(global_default) is provider else provider_default


def available_providers() -> list[dict]:
    """Providers that have an API key configured, with the models each one would use."""
    return [
        {
            "id": provider.id,
            "label": provider.label,
            "transcribe_model": _model_for(provider, settings.transcribe_model, provider.default_transcribe_model),
            "summarize_model": _model_for(provider, settings.summarize_model, provider.default_summarize_model),
        }
        for provider in PROVIDERS
        if provider.api_key
    ]
