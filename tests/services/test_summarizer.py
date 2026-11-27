from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.domain.model_policy import ModelPolicy
from app.services.clients import ClientFactory
from app.services.summarizer import Summarizer


class SummarizerTest:
    def _client(self, content: str | None = "Summary", finish_reason: str = "stop") -> MagicMock:
        choice = MagicMock()
        choice.message.content = content
        choice.finish_reason = finish_reason
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=MagicMock(choices=[choice]))
        return client

    def _summarizer(self, client: MagicMock) -> Summarizer:
        self.clients = MagicMock()
        self.clients.client_for.return_value = client
        return Summarizer(self.clients, ModelPolicy())

    def _sent(self, client: MagicMock) -> dict:
        return client.chat.completions.create.call_args.kwargs


class TestSummarize(SummarizerTest):
    @pytest.mark.asyncio
    async def test_returns_stripped_summary(self):
        client = self._client("  A concise summary  ")
        assert (
            await self._summarizer(client).summarize("Long transcript...", "Summarize", "gpt-4o") == "A concise summary"
        )

    @pytest.mark.asyncio
    async def test_runs_the_model_it_is_given(self):
        client = self._client()
        await self._summarizer(client).summarize("Text", "", "gemini-2.0-flash")
        self.clients.client_for.assert_called_once_with("gemini-2.0-flash")
        assert self._sent(client)["model"] == "gemini-2.0-flash"

    @pytest.mark.asyncio
    async def test_gemini_gets_a_temperature(self):
        client = self._client()
        await self._summarizer(client).summarize("Text", "", "gemini-3-flash-preview")
        assert self._sent(client)["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_openai_omits_temperature(self):
        """OpenAI's GPT-6 models reject any non-default temperature."""
        client = self._client()
        await self._summarizer(client).summarize("Text", "", "gpt-6-luna")
        assert "temperature" not in self._sent(client)

    @pytest.mark.asyncio
    async def test_prompt_and_transcript_go_in_the_user_message(self):
        client = self._client()
        await self._summarizer(client).summarize("Long text", "  Be brief  ", "gpt-4o")
        assert self._sent(client)["messages"][1]["content"] == "Be brief\n\n---\n\nLong text"

    @pytest.mark.asyncio
    async def test_empty_prompt_still_sends_the_transcript(self):
        client = self._client()
        await self._summarizer(client).summarize("Long text", "", "gpt-4o")
        assert "Long text" in self._sent(client)["messages"][1]["content"]


class TestSummarizeErrors(SummarizerTest):
    @pytest.mark.asyncio
    async def test_api_error_propagates(self):
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=Exception("API rate limit"))
        with pytest.raises(Exception, match="API rate limit"):
            await self._summarizer(client).summarize("Transcript text", "Summarize", "gpt-4o")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content", [None, "", "   "])
    async def test_empty_answer_is_an_error(self, content):
        with pytest.raises(RuntimeError, match="empty response"):
            await self._summarizer(self._client(content)).summarize("Text", "", "gpt-4o")

    @pytest.mark.asyncio
    async def test_truncated_answer_is_still_returned(self):
        client = self._client("Partial", finish_reason="length")
        assert await self._summarizer(client).summarize("Text", "", "gpt-4o") == "Partial"

    @pytest.mark.asyncio
    async def test_missing_api_key_at_request_time(self, monkeypatch):
        monkeypatch.setattr(settings, "google_api_key", "")
        policy = ModelPolicy()
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            await Summarizer(ClientFactory(settings, policy), policy).summarize("Text", "Summarize", "gemini-2.0-flash")
