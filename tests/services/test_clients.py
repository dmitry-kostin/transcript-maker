import pytest

from app.config import Settings, settings
from app.domain.model_policy import ModelPolicy
from app.services.clients import ClientFactory, ProviderNotConfigured


@pytest.fixture
def clients() -> ClientFactory:
    return ClientFactory(settings, ModelPolicy())


class TestClientFor:
    def test_openai_client(self, clients, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        assert clients.client_for("gpt-transcribe").base_url.host == "api.openai.com"

    def test_openai_client_missing_key(self, clients, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "")
        with pytest.raises(ProviderNotConfigured, match="TM_OPENAI_API_KEY"):
            clients.client_for("gpt-transcribe")

    def test_gemini_client_with_key(self, clients, monkeypatch):
        monkeypatch.setattr(settings, "google_api_key", "test-key")
        assert "generativelanguage.googleapis.com" in str(clients.client_for("gemini-3.5-transcribe").base_url)

    def test_gemini_client_missing_key(self, clients, monkeypatch):
        monkeypatch.setattr(settings, "google_api_key", "")
        with pytest.raises(ProviderNotConfigured, match="GOOGLE_API_KEY"):
            clients.client_for("gemini-3.5-transcribe")


class TestRequireProvider:
    def test_returns_the_provider_s_configuration(self, clients, monkeypatch):
        monkeypatch.setattr(settings, "google_api_key", "gk-test")
        assert clients.require_provider("gemini-3.5-transcribe").api_key == "gk-test"

    def test_missing_key_is_a_value_error(self, clients, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "")
        with pytest.raises(ValueError, match="OpenAI models"):
            clients.require_provider("gpt-transcribe")


class TestClientLimits:
    """A stalled API call must fail in minutes, not sit for the SDK's 10-minute default."""

    @pytest.mark.parametrize("model", ["gpt-transcribe", "gemini-3.5-transcribe"])
    def test_clients_use_configured_timeout_and_retries(self, clients, model, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(settings, "google_api_key", "gk-test")
        monkeypatch.setattr(settings, "api_timeout_seconds", 42.0)
        monkeypatch.setattr(settings, "api_max_retries", 0)
        client = clients.client_for(model)
        assert client.timeout == 42.0
        assert client.max_retries == 0

    def test_default_timeout_is_well_under_sdk_default(self):
        assert Settings.model_fields["api_timeout_seconds"].default <= 300
