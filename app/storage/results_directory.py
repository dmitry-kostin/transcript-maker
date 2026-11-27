"""Where records and their sidecar files live, and how a record ID maps to them."""

import re
import uuid
from pathlib import Path

from app.config import Settings


class RecordId:
    """Record IDs are 8 lowercase hex chars. Check ``is_valid`` before any file operation."""

    _FORMAT = re.compile(r"[0-9a-f]{8}")
    _IN_FILENAME = re.compile(r"_([0-9a-f]{8})\.md$")

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return bool(cls._FORMAT.fullmatch(value))

    @classmethod
    def new(cls) -> str:
        return uuid.uuid4().hex[:8]

    @classmethod
    def from_filename(cls, name: str) -> str | None:
        """The ID in a record filename (``*_{8hex}.md``); None for any other file."""
        match = cls._IN_FILENAME.search(name)
        return match.group(1) if match else None


class ResultsDirectory:
    """The ``results/`` folder. The only place that builds paths from record IDs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def path(self) -> Path:
        return Path(self._settings.results_dir)

    def ensure(self) -> Path:
        self.path.mkdir(exist_ok=True)
        return self.path

    def markdown_files(self) -> list[Path]:
        """Every .md file — records and summaries alike; ``RecordId.from_filename`` tells them apart."""
        return list(self.ensure().glob("*.md"))

    def new_record_file(self, slug: str, record_id: str) -> Path:
        return self.ensure() / f"{slug}_{record_id}.md"

    def record_file(self, record_id: str) -> Path | None:
        """The .md file of a record. None if the ID is malformed, unknown, or resolves outside the folder."""
        if not RecordId.is_valid(record_id):
            return None
        matches = list(self.ensure().glob(f"*_{record_id}.md"))
        if len(matches) != 1:
            return None
        path = matches[0].resolve()
        if path.parent != self.path.resolve():
            return None
        return path

    def summary_file(self, record_id: str) -> Path:
        return self.path / f"{record_id}_summary.md"

    def chunk_cache_file(self, record_id: str) -> Path:
        return self.path / f"{record_id}_chunks.json"

    def audio_file(self, record_id: str, suffix: str) -> Path:
        return self.path / f"{record_id}{suffix}"

    def existing_audio_file(self, record_id: str) -> Path | None:
        if not RecordId.is_valid(record_id):
            return None
        for path in self.ensure().glob(f"{record_id}.*"):
            if path.suffix != ".md":
                return path
        return None
