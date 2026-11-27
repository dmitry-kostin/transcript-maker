"""Looking at, and getting rid of, what has been transcribed."""

import logging

from app.domain.record import RecordLifecycle
from app.services.desktop import Desktop
from app.storage.history import History
from app.usecases.active_runs import ActiveRuns
from app.usecases.errors import RecordBusy, RecordFromOtherMode, RecordNotFound, SummaryNotFound

logger = logging.getLogger(__name__)


class Records:
    def __init__(self, history: History, desktop: Desktop, active_runs: ActiveRuns) -> None:
        self._history = history
        self._desktop = desktop
        self._active_runs = active_runs

    def list_all(self) -> list[dict]:
        """Every record's metadata, newest first."""
        return self._history.records.list_all()

    def load(self, record_id: str) -> dict:
        record = self._history.records.get(record_id)
        if not record:
            raise RecordNotFound
        return record

    def load_to_change(self, record_id: str, demo: bool) -> dict:
        """The record, for a use case that will rewrite it or its summary.

        A record belongs to the mode that made it: demo mode would replace a real
        transcript with placeholder text, and real mode would spend API calls on one.
        """
        record = self.load(record_id)
        if RecordLifecycle.belongs_to_mode(record, demo):
            return record
        if demo:
            raise RecordFromOtherMode("Demo mode cannot change a real record")
        raise RecordFromOtherMode("This is a demo record — it can only be changed in demo mode")

    def load_summary(self, record_id: str) -> dict:
        summary = self._history.summaries.get(record_id)
        if not summary:
            raise SummaryNotFound
        return summary

    def remove(self, record_id: str) -> None:
        """Delete a record and everything kept beside it — unless a run is transcribing it.

        Deleting under a run would leave it spending API calls on a record that is gone.
        A record merely left in progress, with no run behind it, can be deleted.
        """
        if record_id in self._active_runs.record_ids():
            raise RecordBusy
        if not self._history.records.delete(record_id):
            raise RecordNotFound

    def reveal(self, record_id: str) -> None:
        """Show the record's file in the file manager."""
        path = self._history.records.file_path(record_id)
        if not path:
            raise RecordNotFound
        logger.info("Reveal in Finder: %s", record_id)
        self._desktop.reveal(path)
