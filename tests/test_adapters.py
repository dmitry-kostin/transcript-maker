"""What fills ``Steps``: the services plugged in, their errors translated for the use cases."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters import ConfiguredModels, StepsFactory, StoredAudio, YoutubeDownload
from app.config import settings
from app.domain.model_policy import ModelPolicy
from app.services.clients import ClientFactory
from app.services.demo import DemoServices
from app.services.downloader import DownloadError
from app.usecases.errors import ModelNotAvailable
from app.usecases.steps import DownloadFailed, NoStoredAudio

URL = "https://www.youtube.com/watch?v=abc123xyz"


class TestYoutubeDownload:
    @pytest.mark.asyncio
    async def test_returns_what_the_downloader_fetched(self, tmp_path):
        downloader, accept = MagicMock(), MagicMock()
        downloader.download = AsyncMock(return_value=(tmp_path / "a.mp3", 120, "My Video"))
        assert await YoutubeDownload(downloader).fetch(URL, accept) == (tmp_path / "a.mp3", 120, "My Video")
        downloader.download.assert_awaited_once_with(URL, accept)

    @pytest.mark.asyncio
    async def test_a_download_error_becomes_the_use_cases_own(self):
        downloader = MagicMock()
        downloader.download = AsyncMock(side_effect=DownloadError("Video unavailable"))
        with pytest.raises(DownloadFailed, match="Video unavailable"):
            await YoutubeDownload(downloader).fetch(URL, MagicMock())

    @pytest.mark.asyncio
    async def test_a_refusal_from_accept_passes_through(self):
        """Whatever ``accept`` raises is the use case's own decision, not a failed download."""
        downloader = MagicMock()
        downloader.download = AsyncMock(side_effect=ValueError("too long"))
        with pytest.raises(ValueError, match="too long"):
            await YoutubeDownload(downloader).fetch(URL, MagicMock())


class TestStoredAudio:
    def test_is_the_cache_in_results(self, history, tmp_path):
        audio = tmp_path / "video.mp3"
        audio.write_bytes(b"\x00" * 1024)
        rid = history.records.create("Vid", URL, 60)
        cache = StoredAudio(history)
        assert cache.for_record(rid) is None and cache.for_url(URL) is None
        cache.save(rid, audio)
        assert cache.for_record(rid) == history.audio.path_for(rid)
        found, record = cache.for_url(URL)
        assert (found, record["id"]) == (cache.for_record(rid), rid)


class TestConfiguredModels:
    def test_model_without_an_api_key_is_not_available(self, monkeypatch):
        monkeypatch.setattr(settings, "google_api_key", "")
        with pytest.raises(ModelNotAvailable, match="GOOGLE_API_KEY"):
            ConfiguredModels(ClientFactory(settings, ModelPolicy())).require("gemini-3.5-transcribe")

    def test_model_with_an_api_key_is_available(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        ConfiguredModels(ClientFactory(settings, ModelPolicy())).require("gpt-transcribe")


class TestStepsFactory:
    def test_real_steps_are_the_services_own_methods(self, history):
        downloader, chunker, transcriber, summarizer = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        steps = StepsFactory().real(downloader, chunker, transcriber, summarizer, MagicMock(), history)
        assert steps.prepare_chunks is chunker.prepare_chunks
        assert steps.transcribe_chunk is transcriber.transcribe_chunk
        assert steps.summarize is summarizer.summarize
        assert isinstance(steps.audio_cache, StoredAudio)
        assert steps.demo is False

    @pytest.mark.asyncio
    async def test_real_download_goes_through_the_downloader(self, history, tmp_path):
        downloader = MagicMock()
        downloader.download = AsyncMock(return_value=(tmp_path / "a.mp3", 120, "My Video"))
        steps = StepsFactory().real(downloader, MagicMock(), MagicMock(), MagicMock(), MagicMock(), history)
        assert (await steps.download(URL, MagicMock()))[2] == "My Video"

    def test_demo_steps_are_marked_and_cache_nothing(self):
        steps = StepsFactory().demo(DemoServices(settings))
        assert steps.demo is True
        assert isinstance(steps.audio_cache, NoStoredAudio)
