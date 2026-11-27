from pathlib import Path

import pytest

from app.config import settings
from app.services.temp_files import TempDirectory
from app.usecases.active_runs import ActiveRuns
from app.usecases.maintenance import Maintenance

URL = "https://youtube.com/watch?v=a"


class MaintenanceTest:
    @pytest.fixture(autouse=True)
    def _wired(self, history, tmp_path, monkeypatch):
        self.tmp_dir: Path = tmp_path / "tmp"
        self.tmp_dir.mkdir()
        monkeypatch.setattr(settings, "temp_dir", str(self.tmp_dir))
        self.history = history
        self.active_runs = ActiveRuns()
        self.maintenance = Maintenance(history, TempDirectory(settings), self.active_runs)

    def _temp_file(self, name: str) -> Path:
        file = self.tmp_dir / name
        file.write_bytes(b"\x00")
        return file

    def _status(self, record_id: str) -> str:
        return self.history.records.get(record_id)["status"]


class TestCleanUp(MaintenanceTest):
    def test_deletes_leftover_temp_files_and_fails_records_nothing_is_working_on(self):
        leftover = self._temp_file("abc_11111111.mp3")
        abandoned = self.history.records.create("Abandoned", URL, 60)

        assert self.maintenance.clean_up() == {"deleted_files": 1, "cleaned_records": 1}
        assert not leftover.exists()
        assert self._status(abandoned) == "error"

    def test_leaves_a_running_transcription_and_its_temp_files_alone(self):
        """However long a chunk takes: liveness is known, not guessed from file ages."""
        chunk = self._temp_file("abc_11111111_chunk5.mp3")
        running = self.history.records.create("Running", URL, 3600)
        self.active_runs.start().record_id = running

        assert self.maintenance.clean_up() == {"deleted_files": 0, "cleaned_records": 0}
        assert chunk.exists()
        assert self._status(running) == "in_progress"

    def test_spares_a_download_that_has_no_record_yet(self):
        downloading = self._temp_file("abc_22222222.webm.part")
        self.active_runs.start()
        assert self.maintenance.clean_up()["deleted_files"] == 0
        assert downloading.exists()

    def test_fails_an_abandoned_record_even_while_another_run_is_active(self):
        abandoned = self.history.records.create("Abandoned", URL, 60)
        running = self.history.records.create("Running", URL, 60)
        self.active_runs.start().record_id = running

        assert self.maintenance.clean_up()["cleaned_records"] == 1
        assert self._status(abandoned) == "error"
        assert self._status(running) == "in_progress"


class TestStartupCleanup(MaintenanceTest):
    def test_every_in_progress_record_is_a_leftover(self):
        fresh = self.history.records.create("Fresh", URL, 60)
        done = self.history.records.create("Done", URL, 60)
        self.history.records.complete(done, "text")
        assert self.maintenance.fail_records_left_by_previous_run() == 1
        assert self._status(fresh) == "error"
        assert self._status(done) == "done"
