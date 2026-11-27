"""Transcription records: one markdown file with YAML frontmatter each — no database."""

import logging
import re
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path

from app.storage.audio_cache import AudioCache
from app.storage.chunk_cache import ChunkCache
from app.storage.markdown_file import MarkdownFile
from app.storage.results_directory import RecordId, ResultsDirectory
from app.storage.summary_store import SummaryStore

logger = logging.getLogger(__name__)

STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_ERROR = "error"

SLUG_MAX_LENGTH = 50


class RecordStore:
    """Records are plain dicts; this class is the only thing that reads or writes their files.

    It owns the sidecars' lifetime too: deleting a record deletes its summary,
    cached audio and chunk cache.
    """

    def __init__(
        self,
        directory: ResultsDirectory,
        summaries: SummaryStore,
        audio: AudioCache,
        chunks: ChunkCache,
    ) -> None:
        self._directory = directory
        self._summaries = summaries
        self._audio = audio
        self._chunks = chunks

    # --- lifecycle ---------------------------------------------------------------

    def create(
        self,
        title: str,
        url: str,
        duration: float,
        model: str = "",
        duration_limit: int = 0,
        demo: bool = False,
    ) -> str:
        """Create a record with status in_progress. Returns its ID.

        ``demo`` marks a record made by demo mode, for good: its text is simulated.
        """
        record_id = RecordId.new()
        meta = {
            "title": title,
            "url": url,
            "status": STATUS_IN_PROGRESS,
            "duration": int(duration),
            "duration_limit": duration_limit,
            "model": model,
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "error": "",
        }
        if demo:
            meta["demo"] = True
        MarkdownFile(self._directory.new_record_file(self._slug(title), record_id)).write(meta)
        logger.info("Record created: %s (in_progress) %r", record_id, title)
        return record_id

    def complete(self, record_id: str, text: str) -> bool:
        """Mark a record done and store its transcript. Returns True if written."""
        return self._transition(record_id, STATUS_DONE, body=text)

    def fail(self, record_id: str, error_message: str) -> bool:
        """Mark a record failed. Its previous transcript, if any, stays. Returns True if written."""
        return self._transition(record_id, STATUS_ERROR, error=error_message)

    def reset(self, record_id: str, model: str | None = None, duration_limit: int | None = None) -> bool:
        """Put a finished or failed record back in progress for a re-transcription.

        ``model`` / ``duration_limit`` replace the stored values when given. Returns False
        when the record is missing or something is already working on it.
        """
        return self._transition(record_id, STATUS_IN_PROGRESS, model=model, duration_limit=duration_limit)

    def delete(self, record_id: str) -> bool:
        """Delete a record and everything kept beside it. Returns True if it existed."""
        path = self._directory.record_file(record_id)
        if not path:
            return False
        self._summaries.delete(record_id)
        self._chunks.delete(record_id)
        self._audio.delete(record_id)
        path.unlink(missing_ok=True)
        logger.info("Record deleted: %s", record_id)
        return True

    def fail_in_progress(self, reason: str, except_ids: Collection[str] = ()) -> int:
        """Fail every in_progress record nothing is working on. Returns how many.

        The file cannot tell a live run from a dead one, so the caller names the
        records that are still being worked on in ``except_ids``.
        """
        failed = 0
        for record in self._all():
            if record["status"] != STATUS_IN_PROGRESS or record["id"] in except_ids:
                continue
            if self.fail(record["id"], reason):
                failed += 1
                logger.warning("Cleaned up stale record: %s", record["id"])
        return failed

    # --- queries -----------------------------------------------------------------

    def get(self, record_id: str) -> dict | None:
        """One record with its transcript, or None."""
        path = self._directory.record_file(record_id)
        record = self._parse(path) if path else None
        if record:
            record["has_summary"] = self._summaries.exists(record_id)
        return record

    def list_all(self) -> list[dict]:
        """Every record's metadata (no transcript), newest first."""
        records = self._all()
        for record in records:
            del record["body"]
            record["has_summary"] = self._summaries.exists(record["id"])
        records.sort(key=lambda record: record["created_at"], reverse=True)
        return records

    def status(self, record_id: str) -> str | None:
        record = self.get(record_id)
        return record["status"] if record else None

    def file_path(self, record_id: str) -> Path | None:
        """The record's file — for showing it to the user, never for building other paths."""
        return self._directory.record_file(record_id)

    def find_cached_audio_for_url(self, url: str) -> tuple[Path, dict] | None:
        """Cached audio of any earlier record of the same URL: (audio, that record), or None."""
        for record in self._all():
            if record["url"] != url:
                continue
            audio = self._audio.path_for(record["id"])
            if audio:
                return audio, record
        return None

    # --- internals ---------------------------------------------------------------

    def _all(self) -> list[dict]:
        parsed = (self._parse(path) for path in self._directory.markdown_files())
        return [record for record in parsed if record]

    def _parse(self, path: Path) -> dict | None:
        """A record file as a dict: meta + body + id — never the file path.

        Old records may lack newer fields, so every one has a default.
        """
        record_id = RecordId.from_filename(path.name)
        if not record_id:
            return None
        content = MarkdownFile(path).read()
        if content is None:
            return None
        meta, body = content
        return {
            "id": record_id,
            "title": meta.get("title", "Untitled"),
            "url": meta.get("url", ""),
            "status": meta.get("status", "unknown"),
            "duration": int(meta.get("duration", 0) or 0),
            "duration_limit": int(meta.get("duration_limit", 0) or 0),
            "model": meta.get("model", ""),
            "words": int(meta.get("words", 0) or 0) or (len(body.split()) if body else 0),
            "created_at": meta.get("created_at", ""),
            "error": meta.get("error", ""),
            "demo": meta.get("demo", "") == "True",
            "body": body,
        }

    def _transition(
        self,
        record_id: str,
        status: str,
        *,
        body: str | None = None,
        error: str = "",
        model: str | None = None,
        duration_limit: int | None = None,
    ) -> bool:
        """Move a record to a new status, rewriting its frontmatter. Returns True if written.

        The one place that rebuilds the full meta dict, so every status change keeps all
        fields in the canonical order. ``body`` / ``model`` / ``duration_limit`` keep their
        stored value unless given — so a re-transcription that fails or is interrupted
        leaves the previous transcript in the file; only a successful one replaces it.

        A record that is already in progress cannot be put in progress again: that is
        how two concurrent re-transcriptions of one record are kept apart.
        """
        path = self._directory.record_file(record_id)
        if not path:
            logger.warning("Cannot mark record %s as %s: file not found", record_id, status)
            return False
        stored = self._parse(path)
        if not stored:
            return False
        if status == STATUS_IN_PROGRESS and stored["status"] == STATUS_IN_PROGRESS:
            logger.warning("Record %s is already in progress", record_id)
            return False

        body = stored["body"] if body is None else body
        meta = {
            "title": stored["title"],
            "url": stored["url"],
            "status": status,
            "duration": stored["duration"],
            "duration_limit": stored["duration_limit"] if duration_limit is None else duration_limit,
            "model": stored["model"] if model is None else model,
        }
        if status == STATUS_DONE:
            meta["words"] = len(body.split())
        meta["created_at"] = stored["created_at"]
        meta["error"] = error
        if stored["demo"]:
            meta["demo"] = True
        MarkdownFile(path).write(meta, body)
        logger.info("Record %s: %s", status, record_id)
        return True

    def _slug(self, title: str) -> str:
        """A filesystem-safe piece of the title for the filename."""
        slug = re.sub(r"[^\w\s-]", "", title.lower())
        slug = re.sub(r"[\s_]+", "-", slug).strip("-")
        return slug[:SLUG_MAX_LENGTH] or "untitled"
