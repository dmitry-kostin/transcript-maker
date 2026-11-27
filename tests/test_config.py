import pytest

from app.config import GEMINI_BASE_URL, settings
from app.domain.model_policy import PROVIDERS


class TestProviderSettings:
    def test_every_provider_in_the_table_is_configurable(self):
        """Adding a vendor to PROVIDERS without its settings must fail here, not at the first request."""
        for provider in PROVIDERS:
            assert settings.provider(provider.id).api_key_env

    def test_unknown_provider(self):
        with pytest.raises(KeyError):
            settings.provider("nope")

    def test_reads_the_current_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "google_api_key", "gk-test")
        monkeypatch.setattr(settings, "gemini_transcribe_model", "gemini-custom")
        gemini = settings.provider("gemini")
        assert (gemini.api_key, gemini.base_url, gemini.transcribe_model) == ("gk-test", GEMINI_BASE_URL, "gemini-custom")
        assert settings.provider("openai").base_url is None
