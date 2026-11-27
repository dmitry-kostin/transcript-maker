"""Transcript → summary through a model provider's Chat Completions API."""

import logging

from app.domain.model_policy import ModelPolicy
from app.services.clients import ClientFactory

logger = logging.getLogger(__name__)

SYSTEM_MESSAGE = (
    "You are a helpful assistant that summarizes transcripts. "
    "Produce clear, well-structured summaries. Use bullet points for key points when appropriate. "
    "Be concise but capture all important information."
)


class Summarizer:
    def __init__(self, clients: ClientFactory, policy: ModelPolicy) -> None:
        self._clients = clients
        self._policy = policy

    async def summarize(self, text: str, prompt: str, model: str) -> str:
        """Summarize transcript text.

        ``model`` is an already-resolved model name (see ``ModelPolicy.resolve_summarize_model``).
        """
        client = self._clients.client_for(model)
        logger.info("Summarizing %d chars with model %s", len(text), model)
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": f"{prompt.strip()}\n\n---\n\n{text}"},
            ],
            **self._extra_parameters(model),
        )
        finish_reason = response.choices[0].finish_reason if response.choices else "no_choices"
        content = response.choices[0].message.content if response.choices else None
        if not content or not content.strip():
            logger.error("Summarize API returned empty content (model=%s, finish_reason=%s)", model, finish_reason)
            raise RuntimeError("Summarization returned empty response")
        summary = content.strip()
        if finish_reason != "stop":
            logger.warning("Summarize finish_reason=%s (model=%s, may be truncated)", finish_reason, model)
        logger.info(
            "Summary generated: %d chars, %d words (model=%s, finish_reason=%s)",
            len(summary),
            len(summary.split()),
            model,
            finish_reason,
        )
        return summary

    def _extra_parameters(self, model: str) -> dict:
        """A temperature only where the provider accepts one (OpenAI's GPT-6 models reject non-default values)."""
        temperature = self._policy.provider_for(model).summary_temperature
        return {} if temperature is None else {"temperature": temperature}
