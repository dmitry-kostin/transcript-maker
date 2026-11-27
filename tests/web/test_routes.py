from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services.summarizer import Summarizer


@pytest.fixture
def with_keys(monkeypatch):
    """Both providers configured (dummy keys), default models pinned."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "google_api_key", "gk-test")
    monkeypatch.setattr(settings, "transcribe_model", "gpt-transcribe")
    monkeypatch.setattr(settings, "summarize_model", "gpt-6-luna")


@pytest.fixture
def summarizer(monkeypatch) -> AsyncMock:
    """The real summarizer replaced by a mock. Request it before ``client``: the app binds its services when built."""
    mock = AsyncMock(return_value="Mocked summary")
    monkeypatch.setattr(Summarizer, "summarize", mock)
    return mock


class TestHistoryEndpoints:
    def test_history_empty(self, client):
        res = client.get("/api/history")
        assert res.status_code == 200
        assert res.json() == []

    def test_history_after_create(self, client, tmp_path, monkeypatch, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        res = client.get("/api/history")
        assert res.status_code == 200
        records = res.json()
        assert len(records) == 1
        assert records[0]["id"] == rid

    def test_delete_valid_record(self, client, tmp_path, monkeypatch, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
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
        assert res.json() == {"error": "Not found"}


class TestTranscribeEndpoint:
    def test_missing_api_key_is_refused_before_the_stream_starts(self, client, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "")
        res = client.post("/api/transcribe", json={"url": "https://www.youtube.com/watch?v=abc123xyz", "model": "gpt-transcribe"})
        assert res.status_code == 400
        assert "TM_OPENAI_API_KEY" in res.json()["error"]

    def test_transcribe_invalid_url(self, client):
        res = client.post("/api/transcribe", json={"url": "https://vimeo.com/123"})
        assert res.status_code == 422

    def test_transcribe_empty_url(self, client):
        res = client.post("/api/transcribe", json={"url": ""})
        assert res.status_code == 422

    def test_transcribe_rejects_playlist_url(self, client):
        res = client.post("/api/transcribe", json={"url": "https://www.youtube.com/playlist?list=PLtxgRxNe7rCz"})
        assert res.status_code == 422

    def test_transcribe_rejects_list_without_video(self, client):
        res = client.post("/api/transcribe", json={"url": "https://www.youtube.com/watch?list=PLtxgRxNe7rCz"})
        assert res.status_code == 422


class TestGetRecordEndpoint:
    def test_valid_record(self, client, tmp_path, monkeypatch, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Hello transcript")
        res = client.get(f"/api/history/{rid}")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == rid
        assert data["body"] == "Hello transcript"
        assert "path" not in data

    def test_missing_record(self, client):
        res = client.get("/api/history/00000000")
        assert res.status_code == 404

    def test_invalid_id(self, client):
        res = client.get("/api/history/ZZZZZZZZ")
        assert res.status_code == 400


class TestRetranscribeEndpoint:
    def test_invalid_id(self, client):
        res = client.post("/api/history/ZZZZZZZZ/retranscribe", json={"model": ""})
        assert res.status_code == 400

    def test_nonexistent_id(self, client):
        res = client.post("/api/history/00000000/retranscribe", json={"model": ""})
        assert res.status_code == 404

    def test_in_progress_blocked(self, client, tmp_path, monkeypatch, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        # Record is in_progress by default
        res = client.post(f"/api/history/{rid}/retranscribe", json={"model": ""})
        assert res.status_code == 409
        assert res.json() == {"error": "Record is currently being processed"}

    def test_demo_record_is_refused(self, client, with_keys, history):
        rid = history.records.create("Demo: x", "x", 60, demo=True)
        history.records.complete(rid, "placeholder")
        res = client.post(f"/api/history/{rid}/retranscribe", json={"model": ""})
        assert res.status_code == 409
        assert "demo record" in res.json()["error"]


@pytest.mark.usefixtures("with_keys")
class TestSummarizeEndpoint:
    def test_invalid_id(self, client):
        res = client.post("/api/history/ZZZZZZZZ/summarize", json={"prompt": ""})
        assert res.status_code == 400

    def test_not_done_record(self, client, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        res = client.post(f"/api/history/{rid}/summarize", json={"prompt": ""})
        assert res.status_code == 400

    def test_missing_record(self, client):
        res = client.post("/api/history/00000000/summarize", json={"prompt": ""})
        assert res.status_code == 404

    def test_missing_api_key(self, client, monkeypatch, history):
        monkeypatch.setattr(settings, "openai_api_key", "")
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Text")
        res = client.post(f"/api/history/{rid}/summarize", json={"prompt": ""})
        assert res.status_code == 400
        assert "TM_OPENAI_API_KEY" in res.json()["error"]

    def test_summarizer_failure_is_a_500(self, summarizer, client, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Text")
        summarizer.side_effect = RuntimeError("boom")
        res = client.post(f"/api/history/{rid}/summarize", json={"prompt": ""})
        assert res.status_code == 500
        assert res.json() == {"error": "Summarization failed: boom"}

    def test_successful_summarize(self, summarizer, client, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "This is a test transcript with multiple words.")
        summarizer.return_value = "Mocked summary"
        res = client.post(f"/api/history/{rid}/summarize", json={"prompt": "Custom"})
        assert res.status_code == 200
        data = res.json()
        assert data["summary"] == "# Test\n\nMocked summary"
        assert data["prompt"] == "Custom"
        assert data["model"] == "gpt-6-luna"
        summarizer.assert_awaited_once_with(
            "This is a test transcript with multiple words.", "Custom", "gpt-6-luna"
        )
        # Verify it was saved
        saved = history.summaries.get(rid)
        assert saved is not None
        assert saved["summary"] == "# Test\n\nMocked summary"
        assert saved["model"] == "gpt-6-luna"

    def test_get_summary(self, summarizer, client, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Transcript text")
        summarizer.return_value = "The summary"
        client.post(f"/api/history/{rid}/summarize", json={"prompt": ""})
        res = client.get(f"/api/history/{rid}/summary")
        assert res.status_code == 200
        assert res.json()["summary"] == "# Test\n\nThe summary"

    def test_get_summary_404(self, client, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Text")
        res = client.get(f"/api/history/{rid}/summary")
        assert res.status_code == 404

    def test_delete_cascades_to_summary(self, summarizer, client, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Transcript text for summary test.")
        summarizer.return_value = "Summary text"
        res = client.post(f"/api/history/{rid}/summarize", json={"prompt": ""})
        assert res.status_code == 200
        # Summary exists
        assert client.get(f"/api/history/{rid}/summary").status_code == 200
        # Delete record
        assert client.delete(f"/api/history/{rid}").status_code == 200
        # Summary is gone too
        assert client.get(f"/api/history/{rid}/summary").status_code == 404


class TestDurationLimitParam:
    def test_transcribe_accepts_duration_limit(self, client, with_keys):
        res = client.post("/api/transcribe", json={
            "url": "https://www.youtube.com/watch?v=abc123xyz",
            "duration_limit": 5,
        })
        # Should not be a 422 validation error — the SSE response starts (stream)
        assert res.status_code == 200

    def test_transcribe_rejects_negative_duration_limit(self, client):
        res = client.post("/api/transcribe", json={
            "url": "https://www.youtube.com/watch?v=abc123xyz",
            "duration_limit": -1,
        })
        assert res.status_code == 422

    def test_retranscribe_rejects_negative_duration_limit(self, client, tmp_path, monkeypatch, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 600)
        history.records.complete(rid, "Some text")
        res = client.post(f"/api/history/{rid}/retranscribe", json={
            "model": "",
            "duration_limit": -1,
        })
        assert res.status_code == 422

    def test_transcribe_rejects_excessive_duration_limit(self, client):
        """duration_limit has an upper bound matching the frontend max (Issue #5)."""
        res = client.post("/api/transcribe", json={
            "url": "https://www.youtube.com/watch?v=abc123xyz",
            "duration_limit": 999999,
        })
        assert res.status_code == 422

    def test_retranscribe_rejects_excessive_duration_limit(self, client, tmp_path, monkeypatch, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 600)
        history.records.complete(rid, "Some text")
        res = client.post(f"/api/history/{rid}/retranscribe", json={
            "model": "",
            "duration_limit": 999999,
        })
        assert res.status_code == 422

    def test_duration_limit_zero_means_no_limit(self):
        """duration_limit=0 passes validation — means 'no limit' (Issue #19)."""
        from app.web.schemas import TranscribeRequest
        req = TranscribeRequest(url="https://www.youtube.com/watch?v=abc123xyz", duration_limit=0)
        assert req.duration_limit == 0

    def test_legacy_diarize_field_is_ignored(self):
        """Speaker detection was removed; old clients still sending the flag don't break."""
        from app.web.schemas import TranscribeRequest, RetranscribeRequest
        req = TranscribeRequest(url="https://www.youtube.com/watch?v=abc123xyz", diarize=True)
        assert not hasattr(req, "diarize")
        assert RetranscribeRequest(diarize=True, duration_limit=5).duration_limit == 5


class TestProvidersEndpoint:
    def test_both_keys(self, client, monkeypatch):
        import app.config as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "gk-test")
        res = client.get("/api/providers")
        assert res.status_code == 200
        data = res.json()
        assert len(data["providers"]) == 2
        ids = [p["id"] for p in data["providers"]]
        assert "openai" in ids
        assert "gemini" in ids

    def test_openai_only(self, client, monkeypatch):
        import app.config as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "")
        res = client.get("/api/providers")
        data = res.json()
        assert len(data["providers"]) == 1
        assert data["providers"][0]["id"] == "openai"

    def test_gemini_only(self, client, monkeypatch):
        import app.config as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "gk-test")
        res = client.get("/api/providers")
        data = res.json()
        assert len(data["providers"]) == 1
        assert data["providers"][0]["id"] == "gemini"

    def test_no_keys(self, client, monkeypatch):
        import app.config as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "")
        res = client.get("/api/providers")
        data = res.json()
        assert len(data["providers"]) == 0

    def test_gemini_default_model_used(self, client, monkeypatch):
        import app.config as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "gk-test")
        monkeypatch.setattr(api_mod.settings, "transcribe_model", "gemini-2.5-flash")
        res = client.get("/api/providers")
        data = res.json()
        gemini = next(p for p in data["providers"] if p["id"] == "gemini")
        openai = next(p for p in data["providers"] if p["id"] == "openai")
        # When transcribe_model is Gemini, Gemini provider uses it directly
        assert gemini["transcribe_model"] == "gemini-2.5-flash"
        # OpenAI provider falls back to settings.openai_transcribe_model
        assert openai["transcribe_model"] == api_mod.settings.openai_transcribe_model

    def test_providers_use_per_provider_settings(self, client, monkeypatch):
        """Per-provider model settings are configurable via env vars."""
        import app.config as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "gk-test")
        monkeypatch.setattr(api_mod.settings, "gemini_transcribe_model", "gemini-custom")
        monkeypatch.setattr(api_mod.settings, "gemini_summarize_model", "gemini-custom-sum")
        res = client.get("/api/providers")
        data = res.json()
        gemini = next(p for p in data["providers"] if p["id"] == "gemini")
        assert gemini["transcribe_model"] == "gemini-custom"
        assert gemini["summarize_model"] == "gemini-custom-sum"

    def test_summarize_with_model_override(self, summarizer, client, tmp_path, monkeypatch, with_keys, history):
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Transcript text")
        summarizer.return_value = "Mocked summary"
        res = client.post(f"/api/history/{rid}/summarize", json={"prompt": "Custom", "model": "gemini-2.0-flash"})
        assert res.status_code == 200
        summarizer.assert_awaited_once_with("Transcript text", "Custom", "gemini-2.0-flash")
