"""End-to-end integration tests using real YouTube download + OpenAI Whisper.

These tests require:
- Internet access
- A valid TM_OPENAI_API_KEY in .env
- ffmpeg installed

Run with: poetry run pytest tests/test_integration.py -v
"""
from pathlib import Path

import pytest

from app.config import settings
from app.downloader import download_audio
from app.transcriber import prepare_chunks, transcribe_chunk, cleanup_temp_files

# Test video (~5.5 min)
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=qlkReiRGWpI"


class TestIntegration:
    @pytest.mark.asyncio
    async def test_download_returns_valid_audio(self, tmp_path, monkeypatch):
        import app.downloader as mod
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path))
        path, duration, title = await download_audio(TEST_VIDEO_URL)
        try:
            assert path.exists()
            assert path.stat().st_size > 0
            assert duration > 0
            assert title  # Non-empty
        finally:
            cleanup_temp_files(path)

    @pytest.mark.asyncio
    async def test_full_pipeline_with_history(self, tmp_path, monkeypatch):
        """Single Whisper call covering: transcription, chunking, and history record creation."""
        import app.downloader as mod
        import app.history as history_mod
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        tmp_dir = tmp_path / "tmp"
        tmp_dir.mkdir()
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_dir))
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)

        path, duration, title = await download_audio(TEST_VIDEO_URL)
        try:
            # Chunking
            chunks = prepare_chunks(path)
            assert len(chunks) >= 1

            # Transcription
            texts = []
            for chunk in chunks:
                text = await transcribe_chunk(chunk)
                assert len(text) > 0
                assert isinstance(text, str)
                texts.append(text)
            full = " ".join(texts)
            assert len(full) > 10

            # History record
            rid = history_mod.create_record(title, TEST_VIDEO_URL, duration)
            history_mod.complete_record(rid, full)

            records = history_mod.get_history()
            assert len(records) == 1
            assert records[0]["status"] == "done"

            md_path = history_mod.get_result_path(rid)
            content = md_path.read_text()
            assert full in content
        finally:
            cleanup_temp_files(path)
