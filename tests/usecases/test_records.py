from unittest.mock import MagicMock

import pytest

from app.usecases.active_runs import ActiveRuns
from app.usecases.errors import RecordBusy, RecordFromOtherMode, RecordNotFound, SummaryNotFound
from app.usecases.records import Records

URL = "https://www.youtube.com/watch?v=abc123xyz"


class RecordsTest:
    @pytest.fixture(autouse=True)
    def _wired(self, history):
        self.history = history
        self.desktop = MagicMock()
        self.active_runs = ActiveRuns()
        self.records = Records(history, self.desktop, self.active_runs)


class TestRecords(RecordsTest):
    def test_load_and_list(self):
        rid = self.history.records.create("Vid", URL, 60)
        assert self.records.load(rid)["id"] == rid
        assert [record["id"] for record in self.records.list_all()] == [rid]

    def test_missing_record(self):
        for use in (self.records.load, self.records.remove, self.records.reveal):
            with pytest.raises(RecordNotFound):
                use("00000000")

    def test_remove_takes_the_sidecars_too(self, tmp_results):
        rid = self.history.records.create("Vid", URL, 60)
        self.history.records.complete(rid, "text")
        self.history.summaries.save(rid, "Summary")
        self.records.remove(rid)
        assert self.records.list_all() == []
        assert list(tmp_results.iterdir()) == []

    def test_remove_is_refused_while_a_run_transcribes_the_record(self):
        rid = self.history.records.create("Vid", URL, 60)
        run = self.active_runs.start()
        run.record_id = rid
        with pytest.raises(RecordBusy):
            self.records.remove(rid)
        assert self.records.load(rid)["id"] == rid
        self.active_runs.finish(run)
        self.records.remove(rid)
        assert self.records.list_all() == []

    def test_a_record_left_in_progress_with_no_run_can_be_removed(self):
        rid = self.history.records.create("Vid", URL, 60)
        self.records.remove(rid)
        assert self.records.list_all() == []

    def test_summary(self):
        rid = self.history.records.create("Vid", URL, 60)
        self.history.records.complete(rid, "text")
        with pytest.raises(SummaryNotFound):
            self.records.load_summary(rid)
        self.history.summaries.save(rid, "The summary")
        assert self.records.load_summary(rid)["summary"] == "The summary"

    def test_reveal_shows_the_record_file(self):
        rid = self.history.records.create("Vid", URL, 60)
        self.records.reveal(rid)
        self.desktop.reveal.assert_called_once_with(self.history.records.file_path(rid))


class TestChangingARecord(RecordsTest):
    """A record belongs to the mode that made it."""

    def test_same_mode(self):
        real = self.history.records.create("Real", URL, 60)
        demo = self.history.records.create("Demo", "x", 60, demo=True)
        assert self.records.load_to_change(real, demo=False)["id"] == real
        assert self.records.load_to_change(demo, demo=True)["id"] == demo

    def test_other_mode(self):
        real = self.history.records.create("Real", URL, 60)
        demo = self.history.records.create("Demo", "x", 60, demo=True)
        with pytest.raises(RecordFromOtherMode, match="Demo mode cannot change a real record"):
            self.records.load_to_change(real, demo=True)
        with pytest.raises(RecordFromOtherMode, match="demo record"):
            self.records.load_to_change(demo, demo=False)
