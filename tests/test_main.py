import app.storage.history as history_mod
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage.history import create_record, get_record


class TestStartup:
    def test_building_the_app_does_not_touch_records(self, client):
        """Stale-record cleanup belongs to server startup, not to importing or constructing the app."""
        rid = create_record("Running", "https://youtube.com/watch?v=abc", 60)
        create_app()
        assert get_record(rid)["status"] == "in_progress"

    def test_server_startup_fails_records_left_by_the_previous_run(self, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Left over", "https://youtube.com/watch?v=abc", 60)
        with TestClient(create_app()):
            pass
        record = get_record(rid)
        assert record["status"] == "error"
        assert record["error"] == "Server restarted during transcription"
