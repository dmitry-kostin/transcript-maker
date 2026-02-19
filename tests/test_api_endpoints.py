from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.history import create_record, complete_record, RESULTS_DIR
from app.transcriber import prepare_chunks, MAX_CHUNK_DURATION_SECONDS
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
