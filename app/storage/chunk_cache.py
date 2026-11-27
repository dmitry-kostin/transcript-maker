"""Finished chunk transcriptions of a run in flight (``{record_id}_chunks.json``), so an interrupted run can resume."""

import hashlib
import json
import logging

from app.storage.results_directory import RecordId, ResultsDirectory

logger = logging.getLogger(__name__)


class ChunkCache:
    def __init__(self, directory: ResultsDirectory) -> None:
        self._directory = directory

    def load(self, record_id: str, model: str, total: int, duration_limit: int = 0) -> list[str]:
        """The parts finished so far (may be fewer than ``total``).

        Empty when there is no cache or it was made with another model, chunk count or duration limit.
        """
        path = self._directory.chunk_cache_file(record_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read chunk cache for %s", record_id)
            return []
        if data.get("cache_key") != self._key(model, total, duration_limit):
            logger.info("Chunk cache invalidated for %s (parameter mismatch)", record_id)
            return []
        parts = data.get("parts", [])
        return parts if isinstance(parts, list) else []

    def save(self, record_id: str, model: str, total: int, parts: list[str], duration_limit: int = 0) -> None:
        self._directory.ensure()
        data = {"cache_key": self._key(model, total, duration_limit), "parts": parts}
        self._directory.chunk_cache_file(record_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def delete(self, record_id: str) -> None:
        if RecordId.is_valid(record_id):
            self._directory.chunk_cache_file(record_id).unlink(missing_ok=True)

    def _key(self, model: str, total: int, duration_limit: int) -> str:
        """Hash of everything that makes old parts unusable.

        ``duration_limit`` counts: a different limit can cut the audio into the same
        number of chunks with a different last one.
        """
        raw = f"{model}|{total}|{duration_limit}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
