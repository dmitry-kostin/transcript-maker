"""Demo mode: the real use cases run against the simulated services."""
from pathlib import Path

import pytest

from app.config import settings
from app.storage.history import complete_record, create_record, get_audio_path, get_record, save_audio
from app.usecases.demo import DEMO_SUMMARY, DEMO_TRANSCRIPT, demo_steps, demo_summarize
from app.usecases.summarization import summarize_record
from app.usecases.transcription import run_transcription
from tests.usecases.conftest import URL, never_cancelled


class TestDemoSteps:
    """Demo mode is the same use case with simulated services."""

    @pytest.fixture(autouse=True)
    def _instant(self, monkeypatch):
        import app.usecases.demo as demo
        monkeypatch.setattr(demo, "DEMO_DOWNLOAD_SECONDS", 0)
        monkeypatch.setattr(demo, "DEMO_TRANSCRIBE_SECONDS", 0)

    async def _run(self, **kwargs) -> list[tuple[str, dict]]:
        return [event async for event in run_transcription(
            "anything at all", "", 0, never_cancelled, steps=demo_steps(), **kwargs,
        )]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("multi_chunk_chance", [0.0, 1.0])
    async def test_produces_the_whole_canned_transcript(self, env, monkeypatch, multi_chunk_chance):
        import app.usecases.demo as demo
        monkeypatch.setattr(demo, "DEMO_MULTI_CHUNK_CHANCE", multi_chunk_chance)
        events = await self._run()
        assert events[-1][0] == "done"
        payload = events[-2][1]
        assert payload["text"] == f"Demo: anything at all\n\n{DEMO_TRANSCRIPT}"
        assert get_record(payload["record_id"])["status"] == "done"
        assert list(Path(settings.temp_dir).glob("demo_*")) == []

    @pytest.mark.asyncio
    async def test_placeholder_audio_is_never_cached(self, env):
        """Otherwise a later real transcription of the same URL would pick it up."""
        events = await self._run()
        assert get_audio_path(events[-2][1]["record_id"]) is None

    @pytest.mark.asyncio
    async def test_ignores_real_cached_audio_on_retranscribe(self, env):
        rid = create_record("Real", URL, 90)
        complete_record(rid, "Real\n\nreal text")
        save_audio(rid, env.audio)
        events = await self._run(record=get_record(rid))
        assert events[0][1]["message"] == "Downloading audio from YouTube..."
        assert events[-2][1]["text"] == f"Real\n\n{DEMO_TRANSCRIPT}"


class TestDemoSummarize:
    @pytest.mark.asyncio
    async def test_saves_the_canned_summary(self, tmp_results, monkeypatch):
        import app.usecases.demo as demo
        monkeypatch.setattr(demo, "DEMO_SUMMARIZE_SECONDS", 0)
        rid = create_record("Vid", URL, 60)
        complete_record(rid, "Vid\n\ntext")
        result = await summarize_record(get_record(rid), "", summarize=demo_summarize)
        assert result["summary"] == f"# Vid\n\n{DEMO_SUMMARY}"
