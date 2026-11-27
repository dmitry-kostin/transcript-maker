from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.model_policy import ModelPolicy
from app.services.transcriber import MAX_EMPTY_RESPONSE_RETRIES, Transcriber


class TranscriberTest:
    """A chunk on disk and a transcriber whose API client is a mock."""

    @pytest.fixture(autouse=True)
    def _chunk(self, tmp_path):
        self.chunk = tmp_path / "audio.mp3"
        self.chunk.write_bytes(b"\xff\xd8" * 100)

    def _transcriber(self, client: MagicMock | None = None) -> Transcriber:
        clients = MagicMock()
        clients.client_for.return_value = client
        return Transcriber(clients, ModelPolicy())

    def _chat_client(self, *contents: str | None) -> MagicMock:
        """A client whose chat endpoint answers with each of ``contents`` in turn."""
        responses = []
        for content in contents:
            choice = MagicMock()
            choice.message.content = content
            choice.finish_reason = "stop"
            responses.append(MagicMock(choices=[choice]))
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=responses)
        return client


class TestRouting(TranscriberTest):
    """The provider table decides which API a model is transcribed through."""

    @pytest.mark.asyncio
    async def test_openai_model_uses_the_audio_api(self):
        with patch.object(Transcriber, "_via_audio_api", AsyncMock(return_value="hello")) as audio_api:
            result = await self._transcriber().transcribe_chunk(self.chunk, "gpt-4o-transcribe")
        audio_api.assert_awaited_once_with(self.chunk, "gpt-4o-transcribe")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_gemini_model_uses_chat(self):
        with patch.object(Transcriber, "_via_chat", AsyncMock(return_value="gemini text")) as chat:
            result = await self._transcriber().transcribe_chunk(self.chunk, "gemini-3-flash-preview")
        chat.assert_awaited_once_with(self.chunk, "gemini-3-flash-preview")
        assert result == "gemini text"


class TestViaChat(TranscriberTest):
    @pytest.mark.asyncio
    async def test_verbatim_prompt(self):
        client = self._chat_client("  Hello world  ")
        result = await self._transcriber(client).transcribe_chunk(self.chunk, "gemini-3-flash-preview")
        assert result == "Hello world"
        prompt_text = client.chat.completions.create.call_args.kwargs["messages"][0]["content"][0]["text"]
        assert "verbatim" in prompt_text

    @pytest.mark.asyncio
    async def test_sends_base64_audio_in_the_chunk_s_format(self):
        client = self._chat_client("text")
        await self._transcriber(client).transcribe_chunk(self.chunk, "gemini-3-flash-preview")
        audio_part = client.chat.completions.create.call_args.kwargs["messages"][0]["content"][1]
        assert audio_part["type"] == "input_audio"
        assert len(audio_part["input_audio"]["data"]) > 0
        assert audio_part["input_audio"]["format"] == "mp3"

    @pytest.mark.asyncio
    async def test_sends_no_reasoning_effort(self):
        """gemini-3.5-transcribe is not a reasoning model and rejects the parameter with a 400."""
        client = self._chat_client("text")
        await self._transcriber(client).transcribe_chunk(self.chunk, "gemini-3.5-transcribe")
        assert "reasoning_effort" not in client.chat.completions.create.call_args.kwargs

    @pytest.mark.asyncio
    async def test_empty_answer_is_retried(self, monkeypatch):
        monkeypatch.setattr("app.services.transcriber.asyncio.sleep", AsyncMock())
        client = self._chat_client(None, "second try")
        assert await self._transcriber(client).transcribe_chunk(self.chunk, "gemini-3.5-transcribe") == "second try"
        assert client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_gives_up_after_the_retries(self, monkeypatch):
        monkeypatch.setattr("app.services.transcriber.asyncio.sleep", AsyncMock())
        client = self._chat_client(*[None] * MAX_EMPTY_RESPONSE_RETRIES)
        with pytest.raises(RuntimeError, match="Gemini returned empty content"):
            await self._transcriber(client).transcribe_chunk(self.chunk, "gemini-3.5-transcribe")
        assert client.chat.completions.create.await_count == MAX_EMPTY_RESPONSE_RETRIES


class TestViaAudioApi(TranscriberTest):
    @pytest.mark.asyncio
    async def test_returns_stripped_text(self):
        client = MagicMock()
        client.audio.transcriptions.create = AsyncMock(return_value="  Hello world  ")
        assert await self._transcriber(client).transcribe_chunk(self.chunk, "gpt-4o-transcribe") == "Hello world"
        assert client.audio.transcriptions.create.call_args.kwargs["response_format"] == "text"

    @pytest.mark.asyncio
    async def test_empty_response_is_empty_text(self):
        client = MagicMock()
        client.audio.transcriptions.create = AsyncMock(return_value="")
        assert await self._transcriber(client).transcribe_chunk(self.chunk, "gpt-4o-transcribe") == ""
