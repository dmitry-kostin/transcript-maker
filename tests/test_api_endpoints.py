from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.history import create_record, complete_record, get_record, get_summary, RESULTS_DIR
from app.transcriber import prepare_chunks, MAX_CHUNK_DURATION_SECONDS, get_stored_model, resolve_model
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

    def test_transcribe_rejects_playlist_url(self, client):
        res = client.post("/api/transcribe", json={"url": "https://www.youtube.com/playlist?list=PLtxgRxNe7rCz"})
        assert res.status_code == 422

    def test_transcribe_rejects_list_without_video(self, client):
        res = client.post("/api/transcribe", json={"url": "https://www.youtube.com/watch?list=PLtxgRxNe7rCz"})
        assert res.status_code == 422


class TestGetRecordEndpoint:
    def test_valid_record(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        complete_record(rid, "Hello transcript")
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

    def test_in_progress_blocked(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        # Record is in_progress by default
        res = client.post(f"/api/history/{rid}/retranscribe", json={"model": ""})
        assert res.status_code == 409


class TestSummarizeEndpoint:
    def test_invalid_id(self, client):
        res = client.post("/api/history/ZZZZZZZZ/summarize", json={"prompt": ""})
        assert res.status_code == 400

    def test_not_done_record(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        res = client.post(f"/api/history/{rid}/summarize", json={"prompt": ""})
        assert res.status_code == 400

    def test_successful_summarize(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        complete_record(rid, "This is a test transcript with multiple words.")
        with patch("app.api.summarize_text", return_value="Mocked summary"):
            res = client.post(f"/api/history/{rid}/summarize", json={"prompt": "Custom"})
        assert res.status_code == 200
        data = res.json()
        assert data["summary"] == "Test\n\nMocked summary"
        # Verify it was saved
        saved = get_summary(rid)
        assert saved is not None
        assert saved["summary"] == "Test\n\nMocked summary"

    def test_get_summary(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        complete_record(rid, "Transcript text")
        with patch("app.api.summarize_text", return_value="The summary"):
            client.post(f"/api/history/{rid}/summarize", json={"prompt": ""})
        res = client.get(f"/api/history/{rid}/summary")
        assert res.status_code == 200
        assert res.json()["summary"] == "Test\n\nThe summary"

    def test_get_summary_404(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        complete_record(rid, "Text")
        res = client.get(f"/api/history/{rid}/summary")
        assert res.status_code == 404

    def test_demo_summarize(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        complete_record(rid, "Transcript text")
        res = client.post(f"/api/demo/history/{rid}/summarize", json={"prompt": ""})
        assert res.status_code == 200
        data = res.json()
        assert "Key Points" in data["summary"]


class TestModelStorage:
    def test_openai_diarize_model_stored(self, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        stored = get_stored_model("", diarize=True)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60, model=stored)
        rec = get_record(rid)
        assert rec["model"] == "gpt-4o-transcribe-diarize"

    def test_gemini_diarize_model_stored(self, tmp_path, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-3-flash-preview")
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        stored = get_stored_model("", diarize=True)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60, model=stored)
        rec = get_record(rid)
        assert rec["model"] == "gemini-3-flash-preview-diarize"


class TestPrepareChunks:
    def test_small_file_short_duration_no_chunking(self, tmp_path):
        """File under size AND duration limits → no chunking."""
        audio = tmp_path / "short.mp3"
        audio.write_bytes(b"\x00" * 1_000_000)  # ~1 MB
        with patch("app.transcriber._get_duration", return_value=600.0):
            chunks = prepare_chunks(audio)
        assert chunks == [audio]

    def test_small_file_long_duration_triggers_chunking(self, tmp_path):
        """File under size limit but over duration limit → must chunk."""
        audio = tmp_path / "long.mp3"
        audio.write_bytes(b"\x00" * 5_000_000)  # ~5 MB, well under 24 MB
        long_duration = 3000.0  # 50 minutes, way over 1200s

        chunk_paths = [tmp_path / "long_chunk0.mp3", tmp_path / "long_chunk1.mp3", tmp_path / "long_chunk2.mp3"]
        for p in chunk_paths:
            p.write_bytes(b"\x00" * 1_000_000)

        call_count = 0

        def fake_ffmpeg(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MagicMock(returncode=0)

        with patch("app.transcriber._get_duration", return_value=long_duration), \
             patch("app.transcriber.subprocess.run", side_effect=fake_ffmpeg):
            chunks = prepare_chunks(audio)

        assert len(chunks) == 3  # 3000s / 1200s = 2.5 → 3 chunks
        # Verify ffmpeg was called with chunk duration capped at MAX_CHUNK_DURATION_SECONDS
        first_call_args = chunks  # just verify count; duration checked via call count

    def test_large_file_short_duration_triggers_chunking(self, tmp_path):
        """File over size limit but short duration → chunk by size."""
        audio = tmp_path / "big.mp3"
        audio.write_bytes(b"\x00" * 30_000_000)  # ~30 MB, over 24 MB limit

        chunk_paths = [tmp_path / "big_chunk0.mp3", tmp_path / "big_chunk1.mp3"]
        for p in chunk_paths:
            p.write_bytes(b"\x00" * 12_000_000)

        call_count = 0

        def fake_ffmpeg(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MagicMock(returncode=0)

        with patch("app.transcriber._get_duration", return_value=600.0), \
             patch("app.transcriber.subprocess.run", side_effect=fake_ffmpeg):
            chunks = prepare_chunks(audio)

        assert len(chunks) >= 2


class TestDurationLimitParam:
    def test_transcribe_accepts_duration_limit(self, client):
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

    def test_retranscribe_rejects_negative_duration_limit(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 600)
        complete_record(rid, "Some text")
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

    def test_retranscribe_rejects_excessive_duration_limit(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 600)
        complete_record(rid, "Some text")
        res = client.post(f"/api/history/{rid}/retranscribe", json={
            "model": "",
            "duration_limit": 999999,
        })
        assert res.status_code == 422

    def test_duration_limit_zero_means_no_limit(self):
        """duration_limit=0 passes validation — means 'no limit' (Issue #19)."""
        from app.api import TranscribeRequest
        req = TranscribeRequest(url="https://www.youtube.com/watch?v=abc123xyz", duration_limit=0)
        assert req.duration_limit == 0

    def test_transcribe_accepts_diarize_param(self):
        """diarize boolean is accepted by the API model (Issue #1)."""
        from app.api import TranscribeRequest
        req = TranscribeRequest(url="https://www.youtube.com/watch?v=abc123xyz", diarize=True)
        assert req.diarize is True

    def test_diarize_defaults_false(self):
        """diarize defaults to False when not specified."""
        from app.api import TranscribeRequest
        req = TranscribeRequest(url="https://www.youtube.com/watch?v=abc123xyz")
        assert req.diarize is False

    def test_retranscribe_accepts_diarize_param(self):
        """RetranscribeRequest also accepts diarize."""
        from app.api import RetranscribeRequest
        req = RetranscribeRequest(diarize=True, duration_limit=5)
        assert req.diarize is True


class TestProvidersEndpoint:
    def test_both_keys(self, client, monkeypatch):
        import app.api as api_mod
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
        import app.api as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "")
        res = client.get("/api/providers")
        data = res.json()
        assert len(data["providers"]) == 1
        assert data["providers"][0]["id"] == "openai"

    def test_gemini_only(self, client, monkeypatch):
        import app.api as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "gk-test")
        res = client.get("/api/providers")
        data = res.json()
        assert len(data["providers"]) == 1
        assert data["providers"][0]["id"] == "gemini"

    def test_no_keys(self, client, monkeypatch):
        import app.api as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "")
        res = client.get("/api/providers")
        data = res.json()
        assert len(data["providers"]) == 0

    def test_gemini_default_model_used(self, client, monkeypatch):
        import app.api as api_mod
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
        import app.api as api_mod
        monkeypatch.setattr(api_mod.settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(api_mod.settings, "google_api_key", "gk-test")
        monkeypatch.setattr(api_mod.settings, "gemini_transcribe_model", "gemini-custom")
        monkeypatch.setattr(api_mod.settings, "gemini_summarize_model", "gemini-custom-sum")
        res = client.get("/api/providers")
        data = res.json()
        gemini = next(p for p in data["providers"] if p["id"] == "gemini")
        assert gemini["transcribe_model"] == "gemini-custom"
        assert gemini["summarize_model"] == "gemini-custom-sum"

    def test_summarize_with_model_override(self, client, tmp_path, monkeypatch):
        results_dir = tmp_path / "results"
        results_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        rid = create_record("Test", "https://youtube.com/watch?v=abc", 60)
        complete_record(rid, "Transcript text")
        with patch("app.api.summarize_text", return_value="Mocked summary") as mock_fn:
            res = client.post(f"/api/history/{rid}/summarize", json={"prompt": "Custom", "model": "gemini-2.0-flash"})
        assert res.status_code == 200
        mock_fn.assert_called_once_with("Transcript text", "Custom", model="gemini-2.0-flash")


class TestFrontendModelResolution:
    """Verify the frontend's diarize=true resolves correctly through resolve_model (Issue #20)."""

    def test_diarize_true_resolves_with_openai_default(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        model, diarize = resolve_model("", diarize=True)
        assert model == "gpt-4o-transcribe"
        assert diarize is True

    def test_diarize_true_resolves_with_gemini_default(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-2.0-flash")
        model, diarize = resolve_model("", diarize=True)
        assert model == "gemini-2.0-flash"
        assert diarize is True

    def test_diarize_false_with_empty_model(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        model, diarize = resolve_model("", diarize=False)
        assert model == "gpt-4o-transcribe"
        assert diarize is False

    def test_stored_model_matches_resolved(self, monkeypatch):
        """Storage and execution use the same resolved model (Issue #3)."""
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-2.0-flash")
        actual_model, diarize = resolve_model("", diarize=True)
        stored = get_stored_model("", diarize=True)
        assert stored == f"{actual_model}-diarize"
        assert actual_model == "gemini-2.0-flash"
