"""Shared by the use-case tests: temp dirs and a fake outside world."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.usecases.transcription import Steps, run_transcription

URL = "https://www.youtube.com/watch?v=abc123xyz"


async def never_cancelled() -> bool:
    return False


@pytest.fixture
def env(tmp_results, tmp_path, monkeypatch):
    """Temp dirs plus a fake download, chunker and transcriber, with a ``run()`` that collects events."""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(settings, "temp_dir", str(tmp_dir))
    monkeypatch.setattr(settings, "transcribe_model", "gpt-transcribe")

    audio = tmp_dir / "video_11111111.mp3"
    audio.write_bytes(b"\x00" * 2048)

    class Env:
        def __init__(self) -> None:
            self.audio = audio
            self.chunks: list[Path] | None = None  # None → the audio the use case prepared, unsplit
            self.chunk_limit_seen: int | None = None
            self.download = AsyncMock(return_value=(audio, 120, "My Video"))
            self.transcribe = AsyncMock(side_effect=lambda path, model="": f"text of {Path(path).name}")

        def _prepare_chunks(self, path: Path, duration_limit: int | None) -> list[Path]:
            self.chunk_limit_seen = duration_limit
            return self.chunks or [path]

        def add_chunk(self, name: str) -> Path:
            chunk = audio.with_name(name)
            chunk.write_bytes(b"\x00" * 1024)
            return chunk

        async def run(self, **kwargs) -> list[tuple[str, dict]]:
            kwargs.setdefault("requested_model", "")
            kwargs.setdefault("duration_limit", 0)
            kwargs.setdefault("is_cancelled", never_cancelled)
            steps = Steps(download=self.download, prepare_chunks=self._prepare_chunks, transcribe_chunk=self.transcribe)
            return [event async for event in run_transcription(URL, steps=steps, **kwargs)]

    return Env()
