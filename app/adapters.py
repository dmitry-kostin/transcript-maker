"""What fills the use cases' ``Steps``: the real services, or the simulated ones.

The use cases declare what they need from the outside world (``app.usecases.steps``); this
module plugs the services and stores into it, translating their errors into the ones the
use cases know. Only ``app.wiring`` builds these.
"""

from collections.abc import Callable
from pathlib import Path

from app.services.chunker import AudioChunker
from app.services.clients import ClientFactory, ProviderNotConfigured
from app.services.demo import DemoServices
from app.services.downloader import Downloader, DownloadError
from app.services.summarizer import Summarizer
from app.services.transcriber import Transcriber
from app.storage.history import History
from app.usecases.errors import ModelNotAvailable
from app.usecases.steps import AnyModel, DownloadFailed, NoStoredAudio, Steps


class YoutubeDownload:
    """The download step, done by yt-dlp."""

    def __init__(self, downloader: Downloader) -> None:
        self._downloader = downloader

    async def fetch(self, url: str, accept: Callable[[float], None]) -> tuple[Path, float, str]:
        try:
            return await self._downloader.download(url, accept)
        except DownloadError as e:
            raise DownloadFailed(str(e)) from e


class StoredAudio:
    """The audio cache in ``results/``."""

    def __init__(self, history: History) -> None:
        self._history = history

    def for_record(self, record_id: str) -> Path | None:
        return self._history.audio.path_for(record_id)

    def for_url(self, url: str) -> tuple[Path, dict] | None:
        return self._history.records.find_cached_audio_for_url(url)

    def save(self, record_id: str, audio: Path) -> object:
        return self._history.audio.save(record_id, audio)


class ConfiguredModels:
    """A model can run only if its provider has an API key."""

    def __init__(self, clients: ClientFactory) -> None:
        self._clients = clients

    def require(self, model: str) -> None:
        try:
            self._clients.require_provider(model)
        except ProviderNotConfigured as e:
            raise ModelNotAvailable(str(e)) from e


class StepsFactory:
    def real(
        self,
        downloader: Downloader,
        chunker: AudioChunker,
        transcriber: Transcriber,
        summarizer: Summarizer,
        clients: ClientFactory,
        history: History,
    ) -> Steps:
        return Steps(
            download=YoutubeDownload(downloader).fetch,
            prepare_chunks=chunker.prepare_chunks,
            transcribe_chunk=transcriber.transcribe_chunk,
            summarize=summarizer.summarize,
            check_model=ConfiguredModels(clients).require,
            audio_cache=StoredAudio(history),
        )

    def demo(self, services: DemoServices) -> Steps:
        return Steps(
            download=services.download,
            prepare_chunks=services.prepare_chunks,
            transcribe_chunk=services.transcribe_chunk,
            summarize=services.summarize,
            check_model=AnyModel().require,
            audio_cache=NoStoredAudio(),
            demo=True,
        )
