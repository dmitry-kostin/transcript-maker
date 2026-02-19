"""End-to-end integration tests using real YouTube download + OpenAI Whisper.

These tests require:
- Internet access
- A valid OPENAI_API_KEY in .env or TM_OPENAI_API_KEY env var
- ffmpeg installed

Run with: poetry run pytest tests/test_integration.py -m integration -v
"""
import os

import pytest

# Only set fallback if no real key exists
if not os.environ.get("TM_OPENAI_API_KEY"):
    os.environ["TM_OPENAI_API_KEY"] = "test-key-not-real"

from pathlib import Path
from app.config import settings
from app.downloader import download_audio
from app.transcriber import prepare_chunks, transcribe_chunk, cleanup_temp_files

# Short test video (~10 seconds)
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=qlkReiRGWpI"


def has_real_api_key():
    key = settings.openai_api_key
    return key and key.startswith("sk-") and key != "test-key-not-real"


skip_no_api_key = pytest.mark.skipif(
    not has_real_api_key(),
    reason="No real OpenAI API key configured"
)


@pytest.mark.integration
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

    @skip_no_api_key
    @pytest.mark.asyncio
    async def test_transcribe_real_audio(self, tmp_path, monkeypatch):
        import app.downloader as mod
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path))
        path, duration, title = await download_audio(TEST_VIDEO_URL)
        try:
            text = await transcribe_chunk(path)
            assert len(text) > 0
            assert isinstance(text, str)
        finally:
            cleanup_temp_files(path)

    @skip_no_api_key
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path, monkeypatch):
        import app.downloader as mod
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path))
        path, duration, title = await download_audio(TEST_VIDEO_URL)
        try:
            chunks = prepare_chunks(path)
            assert len(chunks) >= 1
            texts = []
            for chunk in chunks:
                text = await transcribe_chunk(chunk)
                texts.append(text)
            full = " ".join(texts)
            assert len(full) > 10
        finally:
            cleanup_temp_files(path)

    @skip_no_api_key
    @pytest.mark.asyncio
    async def test_full_pipeline_creates_history_record(self, tmp_path, monkeypatch):
        import app.downloader as mod
        import app.history as history_mod
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path / "tmp"))
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
        Path(tmp_path / "tmp").mkdir()

        path, duration, title = await download_audio(TEST_VIDEO_URL)
        try:
            rid = history_mod.create_record(title, TEST_VIDEO_URL, duration)
            chunks = prepare_chunks(path)
            texts = []
            for chunk in chunks:
                text = await transcribe_chunk(chunk)
                texts.append(text)
            full = " ".join(texts)
            history_mod.complete_record(rid, full)

            records = history_mod.get_history()
            assert len(records) == 1
            assert records[0]["status"] == "done"

            # Verify the .md file has the transcript body
            md_path = history_mod.get_result_path(rid)
            content = md_path.read_text()
            assert full in content
        finally:
            cleanup_temp_files(path)
