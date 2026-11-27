"""Model naming and provider rules.

Pure policy: no settings, no network clients, no file access. Everything that
decides *which* model a request means, and what its provider can do, lives here.
How a provider is configured on this machine (API key, endpoint, default models)
is ``settings.provider(provider.id)`` in ``app.config``.
"""
from dataclasses import dataclass

LEGACY_DIARIZE_SUFFIX = "-diarize"

# How a provider takes audio: a dedicated speech-to-text route, or chat with an audio part
TRANSCRIBE_VIA_AUDIO_API = "audio_api"
TRANSCRIBE_VIA_CHAT = "chat"


@dataclass(frozen=True)
class Provider:
    """One model vendor and what it can do."""

    id: str  # also the key of its configuration: ``settings.provider(id)``
    label: str
    model_prefix: str  # "" → the catch-all: any model no other provider claims
    transcribe_via: str
    summary_temperature: float | None  # None → don't send one (GPT-6 rejects non-default values)

    @property
    def is_catch_all(self) -> bool:
        return not self.model_prefix

    def claims(self, model: str) -> bool:
        return not self.is_catch_all and model.startswith(self.model_prefix)


OPENAI = Provider(
    id="openai",
    label="OpenAI",
    model_prefix="",
    transcribe_via=TRANSCRIBE_VIA_AUDIO_API,
    summary_temperature=None,
)

GEMINI = Provider(
    id="gemini",
    label="Gemini",
    model_prefix="gemini-",
    transcribe_via=TRANSCRIBE_VIA_CHAT,
    summary_temperature=0.3,
)

# Order is the order the frontend shows them in
PROVIDERS: tuple[Provider, ...] = (OPENAI, GEMINI)


@dataclass(frozen=True)
class ModelPolicy:
    """Which provider serves a model, and which model a request means."""

    providers: tuple[Provider, ...] = PROVIDERS

    def provider_for(self, model: str) -> Provider:
        """The provider that claims ``model``, else the catch-all."""
        for provider in self.providers:
            if provider.claims(model):
                return provider
        return next(provider for provider in self.providers if provider.is_catch_all)

    def resolve_transcribe_model(self, requested: str, default: str) -> str:
        """The transcription model that will actually run.

        - "" → ``default``
        - explicit model name → that model
        - a legacy "<model>-diarize" name (speaker detection was removed; old records
          still carry these names) → the base model
        """
        return requested.removesuffix(LEGACY_DIARIZE_SUFFIX) or default

    def resolve_summarize_model(self, requested: str, default: str) -> str:
        """The summarization model that will actually run: "" → ``default``."""
        return requested or default

    def default_model_for(self, provider: Provider, global_default: str, provider_default: str) -> str:
        """The model a provider uses when none is asked for.

        The global default wins for the provider it belongs to; others use their own default.
        """
        return global_default if self.provider_for(global_default) is provider else provider_default
