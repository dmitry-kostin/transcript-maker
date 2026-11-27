"""Summaries: one sidecar file ``{record_id}_summary.md`` beside its record."""
import logging
from datetime import datetime, timezone

from app.storage.markdown_file import MarkdownFile
from app.storage.results_directory import RecordId, ResultsDirectory

logger = logging.getLogger(__name__)


class SummaryStore:
    def __init__(self, directory: ResultsDirectory) -> None:
        self._directory = directory

    def save(self, record_id: str, summary: str, prompt: str = "", model: str = "") -> bool:
        """Save a record's summary with the prompt and model that produced it. False if the record is unknown."""
        if not self._directory.record_file(record_id):
            return False
        meta = {
            "prompt": prompt,
            "model": model,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        MarkdownFile(self._directory.summary_file(record_id)).write(meta, summary)
        logger.info("Summary saved for record %s", record_id)
        return True

    def get(self, record_id: str) -> dict | None:
        if not self.exists(record_id):
            return None
        content = MarkdownFile(self._directory.summary_file(record_id)).read()
        if content is None:
            return None
        meta, body = content
        return {
            "prompt": meta.get("prompt", ""),
            "model": meta.get("model", ""),
            "summary": body,
            "created_at": meta.get("created_at", ""),
        }

    def exists(self, record_id: str) -> bool:
        return RecordId.is_valid(record_id) and self._directory.summary_file(record_id).exists()

    def delete(self, record_id: str) -> None:
        if RecordId.is_valid(record_id):
            self._directory.summary_file(record_id).unlink(missing_ok=True)
