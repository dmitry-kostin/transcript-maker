"""The tmp/ folder: where a run keeps its audio and chunks while it works."""

import shutil
import uuid
from pathlib import Path

from app.config import Settings


class TempDirectory:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def path(self) -> Path:
        return Path(self._settings.temp_dir)

    def copy_in(self, source: Path, prefix: str) -> Path:
        """Copy a file into tmp/ under a unique name, so work on it never touches the original. Blocking."""
        self.path.mkdir(exist_ok=True)
        target = self.path / f"{prefix}_{uuid.uuid4().hex[:8]}{source.suffix}"
        shutil.copy2(source, target)
        return target

    def remove_run_files(self, audio_path: Path) -> None:
        """Remove a run's audio and everything cut from it (trimmed copy, chunks share its stem)."""
        for file in audio_path.parent.glob(f"{audio_path.stem}*"):
            file.unlink(missing_ok=True)

    def purge(self) -> int:
        """Delete files directly inside tmp/ (not subdirectories). Returns how many.

        Only call this while nothing is being transcribed — a run keeps its audio and chunks here.
        """
        if not self.path.is_dir():
            return 0
        deleted = 0
        for file in self.path.iterdir():
            if not file.is_file() or file.resolve().parent != self.path.resolve():
                continue
            file.unlink(missing_ok=True)
            deleted += 1
        return deleted
