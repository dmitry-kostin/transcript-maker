"""Where records and their sidecar files live, and how a record ID maps to them."""

from pathlib import Path

from app.config import Settings
from app.domain.record import RecordId


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
