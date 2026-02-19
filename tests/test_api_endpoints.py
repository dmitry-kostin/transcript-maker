import os

os.environ.setdefault("TM_OPENAI_API_KEY", "test-key-not-real")

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.history import create_record, RESULTS_DIR
import app.history as history_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with a temporary results dir."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
    app = create_app()
    return TestClient(app)


class TestHistoryEndpoints:
    def test_history_empty(self, client):
        res = client.get("/api/history")
        assert res.status_code == 200
        assert res.json() == []

    def test_history_after_create(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        res = client.get("/api/history")
        assert res.status_code == 200
        records = res.json()
        assert len(records) == 1
        assert records[0]["id"] == rid

    def test_delete_valid_record(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        res = client.delete(f"/api/history/{rid}")
        assert res.status_code == 200
        assert res.json() == {"ok": True}
        # Verify it's gone
        res2 = client.get("/api/history")
        assert res2.json() == []

    def test_delete_invalid_id(self, client):
        res = client.delete("/api/history/ZZZZZZZZ")
        assert res.status_code == 400

    def test_delete_nonexistent_id(self, client):
        res = client.delete("/api/history/00000000")
        assert res.status_code == 404

    def test_reveal_invalid_id(self, client):
        res = client.post("/api/history/ZZZZZZZZ/reveal")
        assert res.status_code == 400

    def test_reveal_nonexistent_id(self, client):
        res = client.post("/api/history/00000000/reveal")
        assert res.status_code == 404


class TestTranscribeEndpoint:
    def test_transcribe_invalid_url(self, client):
        res = client.post("/api/transcribe", json={"url": "https://vimeo.com/123"})
        assert res.status_code == 422

    def test_transcribe_empty_url(self, client):
        res = client.post("/api/transcribe", json={"url": ""})
        assert res.status_code == 422
