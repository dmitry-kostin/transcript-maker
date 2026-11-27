import pytest

from app.config import settings
from app.domain.model_policy import ModelPolicy
from app.usecases.providers import Providers


@pytest.fixture
def providers() -> Providers:
    return Providers(settings, ModelPolicy())


class TestAvailableProviders:
    def test_only_providers_with_a_key(self, providers, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "")
        monkeypatch.setattr(settings, "google_api_key", "gk-test")
        assert [provider["id"] for provider in providers.available()] == ["gemini"]

    def test_global_default_wins_for_its_own_provider_only(self, providers, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(settings, "google_api_key", "gk-test")
        monkeypatch.setattr(settings, "transcribe_model", "gemini-custom")
        monkeypatch.setattr(settings, "summarize_model", "gpt-custom")
        openai, gemini = providers.available()
        assert openai["transcribe_model"] == settings.openai_transcribe_model
        assert gemini["transcribe_model"] == "gemini-custom"
        assert openai["summarize_model"] == "gpt-custom"
        assert gemini["summarize_model"] == settings.gemini_summarize_model
