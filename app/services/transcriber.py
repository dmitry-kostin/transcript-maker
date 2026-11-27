"""Speech to text through a model provider's API."""
import asyncio
import base64
import logging
from pathlib import Path

from app.domain.model_policy import TRANSCRIBE_VIA_CHAT, ModelPolicy
from app.services.clients import ClientFactory

logger = logging.getLogger(__name__)

MAX_EMPTY_RESPONSE_RETRIES = 3  # chat-based transcription sometimes comes back empty
CHAT_TRANSCRIBE_PROMPT = (
    "Transcribe this audio verbatim as a single continuous block of plain text. "
    "Do not add speaker labels, headings, markdown formatting, or line breaks between sentences."
)


class Transcriber:
    def __init__(self, clients: ClientFactory, policy: ModelPolicy) -> None:
        self._clients = clients
        self._policy = policy

    async def transcribe_chunk(self, chunk_path: Path, model: str) -> str:
        """Send one audio chunk to the transcription API and return the text.

        ``model`` is an already-resolved model name (see ``ModelPolicy.resolve_transcribe_model``).
        """
        chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)
        logger.info("Transcribing %s (%.1f MB) with %s", chunk_path.name, chunk_size_mb, model)

        if self._policy.provider_for(model).transcribe_via == TRANSCRIBE_VIA_CHAT:
            return await self._via_chat(chunk_path, model)
        return await self._via_audio_api(chunk_path, model)

    async def _via_audio_api(self, chunk_path: Path, model: str) -> str:
        """The provider's speech-to-text route — returns plain text."""
        client = self._clients.client_for(model)
        with open(chunk_path, "rb") as audio:
            response = await client.audio.transcriptions.create(model=model, file=audio, response_format="text")
        if not response:
            logger.error("API returned empty response for %s (model=%s, response=%r)", chunk_path.name, model, response)
            return ""
        text = response.strip()
        logger.info("Transcribed %s: %d words, %d chars", chunk_path.name, len(text.split()), len(text))
        return text

    async def _via_chat(self, chunk_path: Path, model: str) -> str:
        """The audio goes in as part of a chat message (how Gemini takes audio). Retries empty answers."""
        client = self._clients.client_for(model)
        label = self._policy.provider_for(model).label
        messages = self._chat_messages(chunk_path)

        for attempt in range(1, MAX_EMPTY_RESPONSE_RETRIES + 1):
            response = await client.chat.completions.create(model=model, messages=messages, temperature=0.0)
            finish_reason = response.choices[0].finish_reason if response.choices else "no_choices"
            content = response.choices[0].message.content if response.choices else None
            usage = self._usage(response)

            if content:
                text = content.strip()
                logger.info("Transcribed %s via %s: %d words, %d chars (finish_reason=%s, usage=%s)",
                            chunk_path.name, label, len(text.split()), len(text), finish_reason, usage)
                if finish_reason != "stop":
                    logger.warning("%s finish_reason=%s for %s (may be truncated)", label, finish_reason, chunk_path.name)
                return text

            logger.warning(
                "%s returned empty content for %s (attempt %d/%d, finish_reason=%s, usage=%s)",
                label, chunk_path.name, attempt, MAX_EMPTY_RESPONSE_RETRIES, finish_reason, usage,
            )
            if attempt < MAX_EMPTY_RESPONSE_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))

        raise RuntimeError(
            f"{label} returned empty content for {chunk_path.name} after {MAX_EMPTY_RESPONSE_RETRIES} attempts"
        )

    def _chat_messages(self, chunk_path: Path) -> list[dict]:
        audio = {
            "data": base64.b64encode(chunk_path.read_bytes()).decode("utf-8"),
            "format": chunk_path.suffix.lstrip("."),
        }
        return [{
            "role": "user",
            "content": [
                {"type": "text", "text": CHAT_TRANSCRIBE_PROMPT},
                {"type": "input_audio", "input_audio": audio},
            ],
        }]

    def _usage(self, response: object) -> str:
        usage = getattr(response, "usage", None)
        return f"prompt={usage.prompt_tokens}, completion={usage.completion_tokens}" if usage else "n/a"
