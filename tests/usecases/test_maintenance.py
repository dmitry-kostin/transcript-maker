import os
import time

import pytest

from app.config import settings
from app.storage.history import complete_record, create_record, get_record, get_result_path
from app.usecases.maintenance import clean_up, fail_records_left_by_previous_run


@pytest.fixture
def tmp_dir(tmp_results, tmp_path, monkeypatch):
    path = tmp_path / "tmp"
    path.mkdir()
    monkeypatch.setattr(settings, "temp_dir", str(path))
    return path


def _age(path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


class TestCleanUp:
    def test_deletes_leftover_temp_files_and_fails_stale_records(self, tmp_dir):
        leftover = tmp_dir / "abc_11111111.mp3"
        leftover.write_bytes(b"\x00")
        _age(leftover, 3600)
        stale = create_record("Stale", "https://youtube.com/watch?v=a", 60)
        _age(get_result_path(stale), 3600)

        assert clean_up() == {"deleted_files": 1, "cleaned_records": 1}
        assert not leftover.exists()
        assert get_record(stale)["status"] == "error"

    def test_leaves_temp_files_alone_while_a_transcription_runs(self, tmp_dir):
        """A running job's chunks sit in tmp/ long before they are transcribed."""
        chunk = tmp_dir / "abc_11111111_chunk5.mp3"
        chunk.write_bytes(b"\x00")
        _age(chunk, 3600)
        running = create_record("Running", "https://youtube.com/watch?v=a", 3600)

        assert clean_up() == {"deleted_files": 0, "cleaned_records": 0}
        assert chunk.exists()
        assert get_record(running)["status"] == "in_progress"

    def test_spares_a_file_that_is_still_being_written(self, tmp_dir):
        downloading = tmp_dir / "abc_22222222.webm.part"
        downloading.write_bytes(b"\x00")
        assert clean_up()["deleted_files"] == 0
        assert downloading.exists()


class TestStartupCleanup:
    def test_every_in_progress_record_is_a_leftover(self, tmp_results):
        fresh = create_record("Fresh", "https://youtube.com/watch?v=a", 60)
        done = create_record("Done", "https://youtube.com/watch?v=b", 60)
        complete_record(done, "text")
        assert fail_records_left_by_previous_run() == 1
        assert get_record(fresh)["status"] == "error"
        assert get_record(done)["status"] == "done"
