import pytest

import app.services.demo as demo
from app.config import settings
from app.services.demo import DEMO_SUMMARY, DEMO_TRANSCRIPT, DemoServices


@pytest.fixture(autouse=True)
def _instant(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    monkeypatch.setattr(demo, "DEMO_DOWNLOAD_SECONDS", 0)
    monkeypatch.setattr(demo, "DEMO_TRANSCRIBE_SECONDS", 0)
    monkeypatch.setattr(demo, "DEMO_SUMMARIZE_SECONDS", 0)


class TestDemoServices:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("multi_chunk_chance", [0.0, 1.0])
    async def test_chunks_share_out_the_whole_canned_text(self, monkeypatch, multi_chunk_chance):
        monkeypatch.setattr(demo, "DEMO_MULTI_CHUNK_CHANCE", multi_chunk_chance)
        services = DemoServices(settings)
        audio, duration, title = await services.download("some url")
        chunks = services.prepare_chunks(audio, None)
        assert (len(chunks) > 1) == bool(multi_chunk_chance)
        parts = [await services.transcribe_chunk(chunk, "any-model") for chunk in chunks]
        assert " ".join(parts) == DEMO_TRANSCRIPT
        assert title == "Demo: some url"

    @pytest.mark.asyncio
    async def test_two_runs_at_once_do_not_mix_their_chunks(self, monkeypatch):
        """One instance serves every request: a run's chunk count comes from its own files."""
        monkeypatch.setattr(demo, "DEMO_MULTI_CHUNK_CHANCE", 1.0)
        services = DemoServices(settings)
        first = services.prepare_chunks((await services.download("a"))[0], None)
        second = services.prepare_chunks((await services.download("b"))[0], None)
        for chunks in (first, second):
            parts = [await services.transcribe_chunk(chunk, "any-model") for chunk in chunks]
            assert " ".join(parts) == DEMO_TRANSCRIPT

    @pytest.mark.asyncio
    async def test_download_reports_the_duration_before_writing_anything(self, tmp_path):
        seen = []
        with pytest.raises(ZeroDivisionError):
            await DemoServices(settings).download("some url", lambda seconds: seen.append(seconds) or 1 / 0)
        assert len(seen) == 1
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_summary_is_canned(self):
        assert await DemoServices(settings).summarize("text", "prompt", "model") == DEMO_SUMMARY
