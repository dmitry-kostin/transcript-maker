import pytest

from app.config import settings
from app.domain.model_policy import (
    GEMINI, OPENAI, available_providers, provider_for, resolve_model, resolve_summarize_model,
)


class TestProviderFor:
    @pytest.mark.parametrize("model", ["gemini-3.5-transcribe", "gemini-3.8-flash", "gemini-2.0-flash"])
    def test_gemini_prefix(self, model):
        assert provider_for(model) is GEMINI

    @pytest.mark.parametrize("model", ["gpt-transcribe", "gpt-6-luna", "whisper-1", ""])
    def test_everything_else_is_openai(self, model):
        assert provider_for(model) is OPENAI

    def test_only_gemini_takes_a_summary_temperature(self):
        """OpenAI's GPT-6 models reject any non-default temperature."""
        assert OPENAI.summary_temperature is None
        assert GEMINI.summary_temperature is not None


class TestResolveModel:
    def test_empty_uses_default(self, monkeypatch):
        monkeypatch.setattr(settings, "transcribe_model", "gpt-transcribe")
        assert resolve_model("") == "gpt-transcribe"

    def test_empty_uses_default_gemini(self, monkeypatch):
        monkeypatch.setattr(settings, "transcribe_model", "gemini-3.5-transcribe")
        assert resolve_model("") == "gemini-3.5-transcribe"

    def test_explicit_model(self):
        assert resolve_model("gpt-transcribe") == "gpt-transcribe"
        assert resolve_model("gemini-3.5-transcribe") == "gemini-3.5-transcribe"

    def test_legacy_diarize_suffix_maps_to_base_model(self):
        """Old records carry '-diarize' model names; they resolve to the plain model."""
        assert resolve_model("gpt-4o-transcribe-diarize") == "gpt-4o-transcribe"
        assert resolve_model("gemini-2.0-flash-diarize") == "gemini-2.0-flash"

    def test_bare_diarize_suffix_uses_default(self, monkeypatch):
        monkeypatch.setattr(settings, "transcribe_model", "gpt-transcribe")
        assert resolve_model("-diarize") == "gpt-transcribe"

    def test_summarize_model_defaults_and_overrides(self, monkeypatch):
        monkeypatch.setattr(settings, "summarize_model", "gpt-6-luna")
        assert resolve_summarize_model("") == "gpt-6-luna"
        assert resolve_summarize_model("gemini-3.8-flash") == "gemini-3.8-flash"


class TestAvailableProviders:
    def test_only_providers_with_a_key(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "")
        monkeypatch.setattr(settings, "google_api_key", "gk-test")
        assert [p["id"] for p in available_providers()] == ["gemini"]

    def test_global_default_wins_for_its_own_provider_only(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(settings, "google_api_key", "gk-test")
        monkeypatch.setattr(settings, "transcribe_model", "gemini-custom")
        monkeypatch.setattr(settings, "summarize_model", "gpt-custom")
        openai, gemini = available_providers()
        assert openai["transcribe_model"] == settings.openai_transcribe_model
        assert gemini["transcribe_model"] == "gemini-custom"
        assert openai["summarize_model"] == "gpt-custom"
        assert gemini["summarize_model"] == settings.gemini_summarize_model
