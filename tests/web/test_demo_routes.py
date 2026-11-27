import pytest

from app.storage.history import complete_record, create_record, get_summary


class TestDemoEndpoints:
    """Demo routes share the real handlers, so they validate and guard the same way."""

    def test_retranscribe_in_progress_blocked(self, client):
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        res = client.post(f"/api/demo/history/{rid}/retranscribe", json={"model": ""})
        assert res.status_code == 409

    def test_retranscribe_invalid_id(self, client):
        res = client.post("/api/demo/history/ZZZZZZZZ/retranscribe", json={})
        assert res.status_code == 400
        assert res.json() == {"error": "Invalid ID"}

    @pytest.mark.parametrize("duration_limit", [-1, 999999, "ten"])
    def test_transcribe_validates_duration_limit(self, client, duration_limit):
        res = client.post("/api/demo/transcribe", json={"url": "anything", "duration_limit": duration_limit})
        assert res.status_code == 422

    def test_summarize_not_done_record(self, client):
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        res = client.post(f"/api/demo/history/{rid}/summarize", json={"prompt": ""})
        assert res.status_code == 400
        assert res.json() == {"error": "Record is not completed"}

    def test_summarize_saves_the_canned_summary(self, client, monkeypatch):
        import app.usecases.demo as demo
        monkeypatch.setattr(demo, "DEMO_SUMMARIZE_SECONDS", 0)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        complete_record(rid, "Transcript text")
        res = client.post(f"/api/demo/history/{rid}/summarize", json={"prompt": "Custom"})
        assert res.status_code == 200
        data = res.json()
        assert data["summary"].startswith("# Test")
        assert "Key Points" in data["summary"]
        assert "prompt" in data
        # Verify summary was saved
        saved = get_summary(rid)
        assert saved is not None


class TestDemoStream:
    def test_demo_transcribe_streams_to_completion(self, client, tmp_path, monkeypatch):
        """The whole path: demo route → shared handler → real use case → SSE."""
        import app.config as config_mod
        import app.usecases.demo as demo
        monkeypatch.setattr(config_mod.settings, "temp_dir", str(tmp_path / "tmp"))
        monkeypatch.setattr(demo, "DEMO_DOWNLOAD_SECONDS", 0)
        monkeypatch.setattr(demo, "DEMO_TRANSCRIBE_SECONDS", 0)

        res = client.post("/api/demo/transcribe", json={"url": "not a url", "duration_limit": 5})
        assert res.status_code == 200
        assert "event: transcript" in res.text
        assert "event: done" in res.text

        (record,) = client.get("/api/history").json()
        assert record["status"] == "done"
        assert record["title"] == "Demo: not a url"
        assert record["duration_limit"] == 300
