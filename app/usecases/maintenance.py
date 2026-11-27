"""Housekeeping: fail records nothing is working on any more, clear leftover temp files."""
import logging

from app.services.temp_files import TempDirectory
from app.storage.history import History
from app.usecases.active_runs import ActiveRuns

logger = logging.getLogger(__name__)


class Maintenance:
    def __init__(self, history: History, temp: TempDirectory, active_runs: ActiveRuns) -> None:
        self._history = history
        self._temp = temp
        self._active_runs = active_runs

    def fail_records_left_by_previous_run(self) -> int:
        """At startup nothing can be running, so every in_progress record is a leftover."""
        return self._history.records.fail_in_progress("Server restarted during transcription")

    def clean_up(self) -> dict:
        """Fail records no run is working on and delete leftover temp files. Safe to call while a transcription runs.

        What is alive comes from ``ActiveRuns``, not from timestamps: a record is
        stale exactly when no run holds it.
        """
        cleaned_records = self._history.records.fail_in_progress(
            "Cleaned up stale record", except_ids=self._active_runs.record_ids(),
        )
        deleted_files = self._purge_temp_files()
        logger.info("Cleanup: deleted %d temp files, cleaned %d stale records", deleted_files, cleaned_records)
        return {"deleted_files": deleted_files, "cleaned_records": cleaned_records}

    def _purge_temp_files(self) -> int:
        """Temp files are left alone while any run exists — its download and chunks live in tmp/."""
        if self._active_runs.any_active():
            logger.info("Cleanup: a transcription is running, temp files left alone")
            return 0
        return self._temp.purge()
