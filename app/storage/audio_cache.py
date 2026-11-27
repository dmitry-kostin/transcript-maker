"""Downloaded audio kept beside its record (``{record_id}.mp3``), so the same video is not fetched twice."""
import logging
import shutil
from pathlib import Path

from app.storage.results_directory import ResultsDirectory

logger = logging.getLogger(__name__)


class AudioCache:
    def __init__(self, directory: ResultsDirectory) -> None:
        self._directory = directory

    def save(self, record_id: str, audio_path: Path) -> Path | None:
        """Copy downloaded audio next to its record. Returns the cached path, or None if it could not be kept."""
        if not self._directory.record_file(record_id):
            return None
        cached = self._directory.audio_file(record_id, audio_path.suffix)
        try:
            shutil.copy2(audio_path, cached)
        except OSError:
            logger.warning("Could not cache audio for %s", record_id)
            return None
        return cached

    def path_for(self, record_id: str) -> Path | None:
        return self._directory.existing_audio_file(record_id)

    def delete(self, record_id: str) -> None:
        cached = self.path_for(record_id)
        if cached:
            cached.unlink(missing_ok=True)
