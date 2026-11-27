"""The summarization use case: completed transcript → summary sidecar."""

import logging

from app.config import Settings
from app.domain.model_policy import ModelPolicy
from app.domain.record import RecordLifecycle
from app.storage.history import History
from app.usecases.errors import RecordNotCompleted, SummaryNotSaved
from app.usecases.records import Records
from app.usecases.steps import Steps

logger = logging.getLogger(__name__)


class Summarization:
    def __init__(
        self,
        steps: Steps,
        history: History,
        records: Records,
        settings: Settings,
        policy: ModelPolicy,
    ) -> None:
        self._steps = steps
        self._history = history
        self._records = records
        self._settings = settings
        self._policy = policy

    async def summarize_record(self, record_id: str, prompt: str, requested_model: str = "") -> dict:
        """Summarize a record's transcript and save it beside the record.

        The model is resolved once here — the same value is run and stored.
        Returns ``{"summary", "prompt", "model"}`` as stored.
        """
        record = self._records.load_to_change(record_id, demo=self._steps.demo)
        if not RecordLifecycle.can_be_summarized(record):
            raise RecordNotCompleted
        model = self._policy.resolve_summarize_model(requested_model, self._settings.summarize_model)
        self._steps.check_model(model)

        prompt = prompt.strip()
        summary = self._with_heading(record["title"], await self._steps.summarize(record["body"], prompt, model))
        if not self._history.summaries.save(record_id, summary, prompt, model):
            raise SummaryNotSaved
        return {"summary": summary, "prompt": prompt, "model": model}

    def _with_heading(self, title: str, summary: str) -> str:
        """A stored summary always starts with the video title as a heading."""
        return f"# {title}\n\n{summary}"
