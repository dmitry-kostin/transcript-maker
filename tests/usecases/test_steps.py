"""Real and demo ``Steps``. Demo mode is the real use cases run against the simulated services."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.services.demo as demo
from app.config import settings
from app.domain.model_policy import ModelPolicy
from app.services.clients import ClientFactory
from app.services.demo import DEMO_SUMMARY, DEMO_TRANSCRIPT
from app.usecases.errors import ModelNotAvailable, RecordFromOtherMode
from app.usecases.steps import AnyModel, ConfiguredModels, NoStoredAudio, Steps, StoredAudio
from tests.usecases.conftest import URL


@pytest.fixture(autouse=True)
def _instant(monkeypatch):
    monkeypatch.setattr(demo, "DEMO_DOWNLOAD_SECONDS", 0)
    monkeypatch.setattr(demo, "DEMO_TRANSCRIBE_SECONDS", 0)
    monkeypatch.setattr(demo, "DEMO_SUMMARIZE_SECONDS", 0)


class DemoTest:
    async def _demo_run(self, env, record_id: str | None = None) -> list[tuple[str, dict]]:
        return await env.run(record_id, steps=env.demo_steps(), url="anything at all")

    def _real_record(self, env) -> str:
        rid = env.history.records.create("Real", URL, 90)
        env.history.records.complete(rid, "Real\n\nreal text")
        env.history.audio.save(rid, env.audio)
        return rid


class TestDemoSteps(DemoTest):
    @pytest.mark.asyncio
    @pytest.mark.parametrize("multi_chunk_chance", [0.0, 1.0])
    async def test_produces_the_whole_canned_transcript(self, env, monkeypatch, multi_chunk_chance):
        monkeypatch.setattr(demo, "DEMO_MULTI_CHUNK_CHANCE", multi_chunk_chance)
        events = await self._demo_run(env)
        assert events[-1][0] == "done"
        payload = events[-2][1]
        assert payload["text"] == f"Demo: anything at all\n\n{DEMO_TRANSCRIPT}"
        record = env.history.records.get(payload["record_id"])
        assert record["status"] == "done"
        assert record["demo"] is True
        assert list(Path(settings.temp_dir).glob("demo_*")) == []

    @pytest.mark.asyncio
    async def test_needs_no_api_key(self, env, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "")
        assert (await self._demo_run(env))[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_placeholder_audio_is_never_cached(self, env):
        """Otherwise a later real transcription of the same URL would pick it up."""
        events = await self._demo_run(env)
        assert env.history.audio.path_for(events[-2][1]["record_id"]) is None

    @pytest.mark.asyncio
    async def test_demo_record_can_be_retranscribed_in_demo_mode(self, env):
        rid = (await self._demo_run(env))[-2][1]["record_id"]
        events = await self._demo_run(env, rid)
        assert events[-2][1]["record_id"] == rid
        assert env.history.records.get(rid)["demo"] is True

    @pytest.mark.asyncio
    async def test_saves_the_canned_summary(self, env):
        rid = env.history.records.create("Vid", "anything", 60, demo=True)
        env.history.records.complete(rid, "Vid\n\ntext")
        result = await env.summarization(env.demo_steps()).summarize_record(rid, "")
        assert result["summary"] == f"# Vid\n\n{DEMO_SUMMARY}"


class TestDemoNeverTouchesRealRecords(DemoTest):
    """A real transcript must not be replaced by placeholder text, nor a real summary by the canned one."""

    @pytest.mark.asyncio
    async def test_retranscribe_is_refused(self, env):
        rid = self._real_record(env)
        with pytest.raises(RecordFromOtherMode):
            await self._demo_run(env, rid)
        untouched = env.history.records.get(rid)
        assert untouched["status"] == "done"
        assert untouched["body"] == "Real\n\nreal text"

    @pytest.mark.asyncio
    async def test_summarize_is_refused(self, env):
        rid = self._real_record(env)
        with pytest.raises(RecordFromOtherMode):
            await env.summarization(env.demo_steps()).summarize_record(rid, "")
        assert env.history.summaries.get(rid) is None


class TestAudioCaches:
    def test_stored_audio_is_the_cache_in_results(self, env):
        rid = env.history.records.create("Vid", URL, 60)
        cache = StoredAudio(env.history)
        assert cache.for_record(rid) is None and cache.for_url(URL) is None
        cache.save(rid, env.audio)
        assert cache.for_record(rid) == env.history.audio.path_for(rid)
        audio, record = cache.for_url(URL)
        assert (audio, record["id"]) == (cache.for_record(rid), rid)

    def test_no_stored_audio_keeps_and_finds_nothing(self, env):
        rid = env.history.records.create("Vid", URL, 60)
        env.history.audio.save(rid, env.audio)  # real audio is there…
        cache = NoStoredAudio()
        cache.save(rid, env.audio)
        assert cache.for_record(rid) is None  # …and still not offered
        assert cache.for_url(URL) is None


class TestModelChecks:
    def test_model_without_an_api_key_is_not_available(self, monkeypatch):
        monkeypatch.setattr(settings, "google_api_key", "")
        with pytest.raises(ModelNotAvailable, match="GOOGLE_API_KEY"):
            ConfiguredModels(ClientFactory(settings, ModelPolicy())).require("gemini-3.5-transcribe")

    def test_model_with_an_api_key_is_available(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        ConfiguredModels(ClientFactory(settings, ModelPolicy())).require("gpt-transcribe")

    def test_any_model_never_refuses(self):
        AnyModel().require("whatever")


class TestStepsWiring:
    def test_real_steps_are_the_services_own_methods(self, history):
        downloader, chunker, transcriber, summarizer = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        steps = Steps.for_real(downloader, chunker, transcriber, summarizer, MagicMock(), history)
        assert steps.download is downloader.download
        assert steps.prepare_chunks is chunker.prepare_chunks
        assert steps.transcribe_chunk is transcriber.transcribe_chunk
        assert steps.summarize is summarizer.summarize
        assert steps.demo is False

    def test_demo_steps_are_marked_and_cache_nothing(self, env):
        steps = env.demo_steps()
        assert steps.demo is True
        assert isinstance(steps.audio_cache, NoStoredAudio)
