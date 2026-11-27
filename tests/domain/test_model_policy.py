import pytest

from app.domain.model_policy import GEMINI, OPENAI, ModelPolicy

policy = ModelPolicy()


class TestProviderFor:
    @pytest.mark.parametrize("model", ["gemini-3.5-transcribe", "gemini-3.8-flash", "gemini-2.0-flash"])
    def test_gemini_prefix(self, model):
        assert policy.provider_for(model) is GEMINI

    @pytest.mark.parametrize("model", ["gpt-transcribe", "gpt-6-luna", "whisper-1", ""])
    def test_everything_else_is_openai(self, model):
        assert policy.provider_for(model) is OPENAI

    def test_only_gemini_takes_a_summary_temperature(self):
        """OpenAI's GPT-6 models reject any non-default temperature."""
        assert OPENAI.summary_temperature is None
        assert GEMINI.summary_temperature is not None


class TestResolveModel:
    def test_empty_uses_default(self):
        assert policy.resolve_transcribe_model("", "gpt-transcribe") == "gpt-transcribe"
        assert policy.resolve_transcribe_model("", "gemini-3.5-transcribe") == "gemini-3.5-transcribe"

    def test_explicit_model(self):
        assert policy.resolve_transcribe_model("gpt-transcribe", "other") == "gpt-transcribe"
        assert policy.resolve_transcribe_model("gemini-3.5-transcribe", "other") == "gemini-3.5-transcribe"

    def test_legacy_diarize_suffix_maps_to_base_model(self):
        """Old records carry '-diarize' model names; they resolve to the plain model."""
        assert policy.resolve_transcribe_model("gpt-4o-transcribe-diarize", "other") == "gpt-4o-transcribe"
        assert policy.resolve_transcribe_model("gemini-2.0-flash-diarize", "other") == "gemini-2.0-flash"

    def test_bare_diarize_suffix_uses_default(self):
        assert policy.resolve_transcribe_model("-diarize", "gpt-transcribe") == "gpt-transcribe"

    def test_summarize_model_defaults_and_overrides(self):
        assert policy.resolve_summarize_model("", "gpt-6-luna") == "gpt-6-luna"
        assert policy.resolve_summarize_model("gemini-3.8-flash", "gpt-6-luna") == "gemini-3.8-flash"


class TestDefaultModelFor:
    def test_global_default_wins_for_its_own_provider_only(self):
        assert policy.default_model_for(GEMINI, "gemini-custom", "gemini-own") == "gemini-custom"
        assert policy.default_model_for(OPENAI, "gemini-custom", "gpt-own") == "gpt-own"
