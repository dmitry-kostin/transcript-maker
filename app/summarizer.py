import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=settings.openai_api_key)

SYSTEM_MESSAGE = (
    "You are a helpful assistant that summarizes transcripts. "
    "Produce clear, well-structured summaries. Use bullet points for key points when appropriate. "
    "Be concise but capture all important information."
)


async def summarize_text(text: str, prompt: str = "") -> str:
    """Summarize transcript text using OpenAI Chat Completions."""
    prompt = prompt.strip()
    user_content = f"{prompt}\n\n---\n\n{text}"

    logger.info("Summarizing %d chars with model %s", len(text), settings.summarize_model)
    response = await client.chat.completions.create(
        model=settings.summarize_model,
        messages=[
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
    )
    summary = response.choices[0].message.content.strip()
    logger.info("Summary generated: %d chars", len(summary))
    return summary
