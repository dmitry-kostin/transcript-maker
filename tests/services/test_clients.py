import pytest

from app.config import Settings, settings
from app.services.clients import get_client


class TestGetClient:
    def test_openai_client(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        client = get_client("gpt-transcribe")
        assert client.base_url.host == "api.openai.com"

    def test_openai_client_missing_key(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "")
        with pytest.raises(ValueError, match="TM_OPENAI_API_KEY"):
            get_client("gpt-transcribe")

    def test_gemini_client_with_key(self, monkeypatch):
        monkeypatch.setattr(settings, "google_api_key", "test-key")
        client = get_client("gemini-3.5-transcribe")
        assert "generativelanguage.googleapis.com" in str(client.base_url)

    def test_gemini_client_missing_key(self, monkeypatch):
        monkeypatch.setattr(settings, "google_api_key", "")
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            get_client("gemini-3.5-transcribe")


class TestClientLimits:
    """A stalled API call must fail in minutes, not sit for the SDK's 10-minute default."""

    @pytest.mark.parametrize("model", ["gpt-transcribe", "gemini-3.5-transcribe"])
    def test_clients_use_configured_timeout_and_retries(self, model, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(settings, "google_api_key", "gk-test")
        monkeypatch.setattr(settings, "api_timeout_seconds", 42.0)
        monkeypatch.setattr(settings, "api_max_retries", 0)
        client = get_client(model)
        assert client.timeout == 42.0
        assert client.max_retries == 0

    def test_default_timeout_is_well_under_sdk_default(self):
        assert Settings.model_fields["api_timeout_seconds"].default <= 300
