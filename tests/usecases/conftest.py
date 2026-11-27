"""Shared by the use-case tests: temp dirs, a fake outside world, and use cases built on them."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters import StepsFactory, StoredAudio
from app.config import settings
from app.domain.limits import DurationLimits
from app.domain.model_policy import ModelPolicy
from app.services.demo import DemoServices
from app.services.temp_files import TempDirectory
from app.storage.history import History
from app.usecases.active_runs import ActiveRuns
from app.usecases.records import Records
from app.usecases.steps import Steps
from app.usecases.summarization import Summarization
from app.usecases.transcription import RunTools, Transcription

URL = "https://www.youtube.com/watch?v=abc123xyz"


class Env:
    """The use cases wired like the app wires them, with a fake download, chunker, transcriber and summarizer."""

    def __init__(self, history: History, audio: Path) -> None:
        self.history = history
        self.audio = audio
        self.active_runs = ActiveRuns()
        self.desktop = MagicMock()
        self.records = Records(history, self.desktop, self.active_runs)
        self.chunks: list[Path] | None = None  # None → the audio the use case prepared, unsplit
        self.chunk_limit_seen: int | None = None
        self.download = AsyncMock(return_value=(audio, 120, "My Video"))
        self.transcribe = AsyncMock(side_effect=lambda path, model: f"text of {Path(path).name}")
        self.summarize = AsyncMock(return_value="The summary")
        self.check_model = MagicMock()  # raise from it to make a model unavailable

    def add_chunk(self, name: str) -> Path:
        chunk = self.audio.with_name(name)
        chunk.write_bytes(b"\x00" * 1024)
        return chunk

    def steps(self) -> Steps:
        return Steps(
            download=self.download,
            prepare_chunks=self._prepare_chunks,
            transcribe_chunk=self.transcribe,
            summarize=self.summarize,
            check_model=self.check_model,
            audio_cache=StoredAudio(self.history),
        )

    def demo_steps(self) -> Steps:
        return StepsFactory().demo(DemoServices(settings))

    def transcription(self, steps: Steps | None = None) -> Transcription:
        tools = RunTools(
            steps or self.steps(), self.history, TempDirectory(settings), self.active_runs, DurationLimits()
        )
        return Transcription(tools, self.records, settings, ModelPolicy())

    def summarization(self, steps: Steps | None = None) -> Summarization:
        return Summarization(steps or self.steps(), self.history, self.records, settings, ModelPolicy())

    async def run(
        self,
        record_id: str | None = None,
        steps: Steps | None = None,
        url: str = URL,
        requested_model: str = "",
        duration_limit: int = 0,
        is_cancelled=None,
    ) -> list[tuple[str, dict]]:
        """A new transcription of ``url``, or a re-transcription of ``record_id``; returns its events."""
        transcription = self.transcription(steps)
        options = (requested_model, duration_limit, is_cancelled or self._never_cancelled)
        events = (
            transcription.retranscribe(record_id, *options)
            if record_id
            else transcription.transcribe_url(url, *options)
        )
        return [event async for event in events]

    def _prepare_chunks(self, path: Path, duration_limit: int | None) -> list[Path]:
        self.chunk_limit_seen = duration_limit
        return self.chunks or [path]

    async def _never_cancelled(self) -> bool:
        return False


@pytest.fixture
def env(history, tmp_path, monkeypatch) -> Env:
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(settings, "temp_dir", str(tmp_dir))
    monkeypatch.setattr(settings, "transcribe_model", "gpt-transcribe")
    monkeypatch.setattr(settings, "summarize_model", "gpt-6-luna")

    audio = tmp_dir / "video_11111111.mp3"
    audio.write_bytes(b"\x00" * 2048)
    return Env(history, audio)
