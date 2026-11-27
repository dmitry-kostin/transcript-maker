"""The outside world as the use cases see it: a bundle of operations, real or simulated.

``Steps.for_real()`` talks to YouTube, ffmpeg and the model APIs; ``Steps.for_demo()``
simulates all of it. The use cases never know which one they were given.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.services.chunker import AudioChunker
from app.services.clients import ClientFactory, ProviderNotConfigured
from app.services.demo import DemoServices
from app.services.downloader import Downloader
from app.services.summarizer import Summarizer
from app.services.transcriber import Transcriber
from app.storage.history import History
from app.usecases.errors import ModelNotAvailable


class CachedAudio(Protocol):
    """Downloaded audio kept so the same video is not fetched twice."""

    def for_record(self, record_id: str) -> Path | None: ...

    def for_url(self, url: str) -> tuple[Path, dict] | None:
        """(cached audio, the record it belongs to) of an earlier transcription of ``url``."""

    def save(self, record_id: str, audio: Path) -> object: ...


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


class NoStoredAudio:
    """Simulated runs keep nothing and find nothing: placeholder audio must never be
    picked up by a real transcription of the same URL, nor real audio by a simulated one.
    """

    def for_record(self, record_id: str) -> Path | None:
        return None

    def for_url(self, url: str) -> tuple[Path, dict] | None:
        return None

    def save(self, record_id: str, audio: Path) -> object:
        return None


class ConfiguredModels:
    """A model can run only if its provider has an API key."""

    def __init__(self, clients: ClientFactory) -> None:
        self._clients = clients

    def require(self, model: str) -> None:
        try:
            self._clients.require_provider(model)
        except ProviderNotConfigured as e:
            raise ModelNotAvailable(str(e)) from e


class AnyModel:
    """No API is called, so every model is fine and no key is needed."""

    def require(self, model: str) -> None:
        return None


@dataclass(frozen=True)
class Steps:
    """The outside-world work the use cases depend on. Swap these to run them without real services."""

    # (url, accept) → (audio, duration, title). ``accept(duration)`` may raise to refuse before anything is fetched
    download: Callable[[str, Callable[[float], None]], Awaitable[tuple[Path, float, str]]]
    prepare_chunks: Callable[[Path, int | None], list[Path]]  # (audio, duration_limit) → chunks; blocking
    transcribe_chunk: Callable[[Path, str], Awaitable[str]]  # (chunk, model) → text
    summarize: Callable[[str, str, str], Awaitable[str]]  # (text, prompt, model) → summary
    check_model: Callable[[str], None]  # raises ModelNotAvailable when ``model`` cannot be run
    audio_cache: CachedAudio
    # Records made with these steps are demo records, and only such steps may change them
    demo: bool = False

    @classmethod
    def for_real(
        cls, downloader: Downloader, chunker: AudioChunker, transcriber: Transcriber,
        summarizer: Summarizer, clients: ClientFactory, history: History,
    ) -> "Steps":
        return cls(
            download=downloader.download,
            prepare_chunks=chunker.prepare_chunks,
            transcribe_chunk=transcriber.transcribe_chunk,
            summarize=summarizer.summarize,
            check_model=ConfiguredModels(clients).require,
            audio_cache=StoredAudio(history),
        )

    @classmethod
    def for_demo(cls, services: DemoServices) -> "Steps":
        return cls(
            download=services.download,
            prepare_chunks=services.prepare_chunks,
            transcribe_chunk=services.transcribe_chunk,
            summarize=services.summarize,
            check_model=AnyModel().require,
            audio_cache=NoStoredAudio(),
            demo=True,
        )
