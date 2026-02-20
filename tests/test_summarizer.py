from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from app.clients import is_gemini_model, get_client
from app.summarizer import summarize_text


class TestIsGeminiModelSummarizer:
    def test_gemini_model(self):
        assert is_gemini_model("gemini-3-flash-preview") is True

    def test_gemini_pro(self):
        assert is_gemini_model("gemini-2.0-flash") is True

    def test_openai_model(self):
        assert is_gemini_model("gpt-4o") is False

    def test_empty_string(self):
        assert is_gemini_model("") is False


class TestGetClientSummarizer:
    def test_openai_client(self):
        client = get_client("gpt-4o")
        assert client.base_url.host == "api.openai.com"

    def test_gemini_client_with_key(self, monkeypatch):
        import app.clients as mod
        monkeypatch.setattr(mod.settings, "google_api_key", "test-key")
        client = get_client("gemini-3-flash-preview")
        assert "generativelanguage.googleapis.com" in str(client.base_url)

    def test_gemini_client_missing_key(self, monkeypatch):
        import app.clients as mod
        monkeypatch.setattr(mod.settings, "google_api_key", "")
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            get_client("gemini-3-flash-preview")


class TestSummarizeText:
    @pytest.mark.asyncio
    async def test_returns_stripped_summary(self, monkeypatch):
        import app.summarizer as mod
        monkeypatch.setattr(mod.settings, "summarize_model", "gpt-4o")
        mock_msg = MagicMock()
        mock_msg.content = "  A concise summary  "
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        with patch("app.summarizer.get_client", return_value=mock_client):
            result = await summarize_text("Long transcript...", prompt="Summarize")
        assert result == "A concise summary"

    @pytest.mark.asyncio
    async def test_passes_correct_model(self, monkeypatch):
        import app.summarizer as mod
        monkeypatch.setattr(mod.settings, "summarize_model", "gemini-3-flash-preview")
        mock_msg = MagicMock()
        mock_msg.content = "Summary"
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        with patch("app.summarizer.get_client", return_value=mock_client):
            await summarize_text("Text", prompt="")
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gemini-3-flash-preview"


class TestSummarizeModelOverride:
    @pytest.mark.asyncio
    async def test_explicit_model_overrides_default(self, monkeypatch):
        import app.summarizer as mod
        monkeypatch.setattr(mod.settings, "summarize_model", "gpt-4o")
        mock_msg = MagicMock()
        mock_msg.content = "Summary"
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        with patch("app.summarizer.get_client", return_value=mock_client) as get_mock:
            await summarize_text("Text", model="gemini-2.0-flash")
        get_mock.assert_called_once_with("gemini-2.0-flash")
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.0-flash"

    @pytest.mark.asyncio
    async def test_empty_model_uses_default(self, monkeypatch):
        import app.summarizer as mod
        monkeypatch.setattr(mod.settings, "summarize_model", "gpt-4o")
        mock_msg = MagicMock()
        mock_msg.content = "Summary"
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        with patch("app.summarizer.get_client", return_value=mock_client) as get_mock:
            await summarize_text("Text", model="")
        get_mock.assert_called_once_with("gpt-4o")


class TestSummarizeErrors:
    """Error path tests for the summarizer (Issue #18)."""

    @pytest.mark.asyncio
    async def test_api_error_propagates(self, monkeypatch):
        import app.summarizer as mod
        monkeypatch.setattr(mod.settings, "summarize_model", "gpt-4o")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API rate limit"))
        with patch("app.summarizer.get_client", return_value=mock_client):
            with pytest.raises(Exception, match="API rate limit"):
                await summarize_text("Transcript text", prompt="Summarize")

    @pytest.mark.asyncio
    async def test_empty_transcript_handled(self, monkeypatch):
        import app.summarizer as mod
        monkeypatch.setattr(mod.settings, "summarize_model", "gpt-4o")
        mock_msg = MagicMock()
        mock_msg.content = "No content to summarize."
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        with patch("app.summarizer.get_client", return_value=mock_client):
            result = await summarize_text("", prompt="Summarize")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_missing_api_key_at_request_time(self, monkeypatch):
        import app.summarizer as mod
        monkeypatch.setattr(mod.settings, "summarize_model", "gemini-2.0-flash")
        import app.clients as clients_mod
        monkeypatch.setattr(clients_mod.settings, "google_api_key", "")
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            await summarize_text("Text", prompt="Summarize")

    @pytest.mark.asyncio
    async def test_empty_prompt_uses_default(self, monkeypatch):
        import app.summarizer as mod
        monkeypatch.setattr(mod.settings, "summarize_model", "gpt-4o")
        mock_msg = MagicMock()
        mock_msg.content = "Summary text"
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        with patch("app.summarizer.get_client", return_value=mock_client):
            result = await summarize_text("Long text", prompt="")
        assert result == "Summary text"
        # Verify empty prompt still sends content (separator + text)
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "Long text" in user_msg
