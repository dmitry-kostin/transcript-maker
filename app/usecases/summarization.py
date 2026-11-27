"""The summarization use case: completed transcript → summary sidecar."""
import logging
from collections.abc import Awaitable, Callable

from app.services.summarizer import summarize_text
from app.storage.history import STATUS_DONE, save_summary

logger = logging.getLogger(__name__)


class RecordNotCompleted(Exception):
    """Only a finished transcript can be summarized."""


class SummaryNotSaved(Exception):
    """The summary was generated but could not be written."""


def with_heading(title: str, summary: str) -> str:
    """A stored summary always starts with the video title as a heading."""
    return f"# {title}\n\n{summary}"


async def summarize_record(
    record: dict,
    prompt: str,
    model: str = "",
    summarize: Callable[..., Awaitable[str]] | None = None,
) -> dict:
    """Summarize ``record``'s transcript and save it beside the record.

    ``summarize`` replaces the real summarizer (demo mode passes a canned one).
    Returns ``{"summary", "prompt"}`` as stored.
    """
    if record["status"] != STATUS_DONE:
        raise RecordNotCompleted(record["id"])
    summarize = summarize or summarize_text
    summary = with_heading(record["title"], await summarize(record["body"], prompt, model=model))
    prompt = prompt.strip()
    if not save_summary(record["id"], summary, prompt):
        raise SummaryNotSaved(record["id"])
    return {"summary": summary, "prompt": prompt}
