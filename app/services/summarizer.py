import logging

from app.domain.model_policy import provider_for, resolve_summarize_model
from app.services.clients import get_client

logger = logging.getLogger(__name__)

SYSTEM_MESSAGE = (
    "You are a helpful assistant that summarizes transcripts. "
    "Produce clear, well-structured summaries. Use bullet points for key points when appropriate. "
    "Be concise but capture all important information."
)


async def summarize_text(text: str, prompt: str = "", model: str = "") -> str:
    """Summarize transcript text using Chat Completions."""
    prompt = prompt.strip()
    user_content = f"{prompt}\n\n---\n\n{text}"

    model = resolve_summarize_model(model)
    client = get_client(model)
    logger.info("Summarizing %d chars with model %s", len(text), model)
    # Only sent where the provider accepts one (OpenAI's GPT-6 models reject non-default values)
    temperature = provider_for(model).summary_temperature
    extra = {} if temperature is None else {"temperature": temperature}
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": user_content},
        ],
        **extra,
    )
    finish_reason = response.choices[0].finish_reason if response.choices else "no_choices"
    content = response.choices[0].message.content if response.choices else None
    if not content or not content.strip():
        logger.error("Summarize API returned empty content (model=%s, finish_reason=%s)", model, finish_reason)
        raise RuntimeError("Summarization returned empty response")
    summary = content.strip()
    if finish_reason != "stop":
        logger.warning("Summarize finish_reason=%s (model=%s, may be truncated)", finish_reason, model)
    logger.info("Summary generated: %d chars, %d words (model=%s, finish_reason=%s)", len(summary), len(summary.split()), model, finish_reason)
    return summary
