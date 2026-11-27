"""The outside world as the use cases see it: a bundle of operations, real or simulated.

This is the port; what fills it is built in ``app.adapters`` (``StepsFactory.real()`` talks to
YouTube, ffmpeg and the model APIs, ``StepsFactory.demo()`` simulates all of it). The use cases
never know which one they were given, and nothing here knows a service.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class DownloadFailed(Exception):
    """The ``download`` step could not fetch the audio. Its text is shown to the user."""


class CachedAudio(Protocol):
    """Downloaded audio kept so the same video is not fetched twice."""

    def for_record(self, record_id: str) -> Path | None: ...

    def for_url(self, url: str) -> tuple[Path, dict] | None:
        """(cached audio, the record it belongs to) of an earlier transcription of ``url``."""

    def save(self, record_id: str, audio: Path) -> object: ...


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


class AnyModel:
    """No API is called, so every model is fine and no key is needed."""

    def require(self, model: str) -> None:
        return None


@dataclass(frozen=True)
class Steps:
    """The outside-world work the use cases depend on. Swap these to run them without real services."""

    # (url, accept) → (audio, duration, title). ``accept(duration)`` may raise to refuse before anything is
    # fetched; a download that cannot be done raises ``DownloadFailed``
    download: Callable[[str, Callable[[float], None]], Awaitable[tuple[Path, float, str]]]
    prepare_chunks: Callable[[Path, int | None], list[Path]]  # (audio, duration_limit) → chunks; blocking
    transcribe_chunk: Callable[[Path, str], Awaitable[str]]  # (chunk, model) → text
    summarize: Callable[[str, str, str], Awaitable[str]]  # (text, prompt, model) → summary
    check_model: Callable[[str], None]  # raises ModelNotAvailable when ``model`` cannot be run
    audio_cache: CachedAudio
    # Records made with these steps are demo records, and only such steps may change them
    demo: bool = False
