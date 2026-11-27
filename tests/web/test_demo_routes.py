import pytest

import app.services.demo as demo
from app.config import settings


@pytest.fixture(autouse=True)
def _instant(monkeypatch):
    monkeypatch.setattr(demo, "DEMO_DOWNLOAD_SECONDS", 0)
    monkeypatch.setattr(demo, "DEMO_TRANSCRIBE_SECONDS", 0)
    monkeypatch.setattr(demo, "DEMO_SUMMARIZE_SECONDS", 0)


class DemoRoutesTest:
    @pytest.fixture(autouse=True)
    def _history(self, history):
        self.history = history

    def _demo_record(self, done: bool = True) -> str:
        rid = self.history.records.create("Test", "anything", 60, demo=True)
        if done:
            self.history.records.complete(rid, "Transcript text")
        return rid

    def _real_record(self) -> str:
        rid = self.history.records.create("Real", "https://youtube.com/watch?v=abc", 60)
        self.history.records.complete(rid, "Real transcript")
        return rid


class TestDemoEndpoints(DemoRoutesTest):
    """Demo routes are the real handlers built with demo steps, so they validate and guard the same way."""

    def test_retranscribe_in_progress_blocked(self, client):
        res = client.post(f"/api/demo/history/{self._demo_record(done=False)}/retranscribe", json={"model": ""})
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
        res = client.post(f"/api/demo/history/{self._demo_record(done=False)}/summarize", json={"prompt": ""})
        assert res.status_code == 400
        assert res.json() == {"error": "Record is not completed"}

    def test_summarize_saves_the_canned_summary(self, client, history):
        rid = self._demo_record()
        res = client.post(f"/api/demo/history/{rid}/summarize", json={"prompt": "Custom"})
        assert res.status_code == 200
        data = res.json()
        assert data["summary"].startswith("# Test")
        assert "Key Points" in data["summary"]
        assert data["prompt"] == "Custom"
        assert history.summaries.get(rid) is not None


class TestDemoLeavesRealRecordsAlone(DemoRoutesTest):
    def test_retranscribe_real_record_is_refused(self, client, history):
        rid = self._real_record()
        res = client.post(f"/api/demo/history/{rid}/retranscribe", json={})
        assert res.status_code == 409
        assert res.json() == {"error": "Demo mode cannot change a real record"}
        assert history.records.get(rid)["body"] == "Real transcript"

    def test_summarize_real_record_is_refused(self, client, history):
        rid = self._real_record()
        res = client.post(f"/api/demo/history/{rid}/summarize", json={"prompt": ""})
        assert res.status_code == 409
        assert history.summaries.get(rid) is None


class TestDemoStream(DemoRoutesTest):
    def test_demo_transcribe_streams_to_completion(self, client, tmp_path, monkeypatch):
        """The whole path: demo route → real use case with demo steps → SSE. No API key needed."""
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path / "tmp"))
        monkeypatch.setattr(settings, "openai_api_key", "")

        res = client.post("/api/demo/transcribe", json={"url": "not a url", "duration_limit": 5})
        assert res.status_code == 200
        assert "event: transcript" in res.text
        assert "event: done" in res.text

        (record,) = client.get("/api/history").json()
        assert record["status"] == "done"
        assert record["demo"] is True
        assert record["title"] == "Demo: not a url"
        assert record["duration_limit"] == 300

    def test_demo_retranscribe_streams_to_completion(self, client, tmp_path, monkeypatch, history):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path / "tmp"))
        rid = self._demo_record()
        res = client.post(f"/api/demo/history/{rid}/retranscribe", json={})
        assert res.status_code == 200
        assert "event: done" in res.text
        assert history.records.get(rid)["status"] == "done"
