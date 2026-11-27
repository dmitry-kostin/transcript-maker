"""Simulated services for demo mode.

Nothing here talks to YouTube or a model API. ``StepsFactory.demo()`` hands these to the
real use cases, so records, progress events, cancellation and failure handling
behave exactly as they do for real.
"""

import asyncio
import random
import uuid
from collections.abc import Callable
from pathlib import Path

from app.config import Settings

DEMO_TRANSCRIPT = (
    "This is a simulated transcript generated in demo mode. "
    "No real YouTube download or OpenAI API call was made. "
    "The quick brown fox jumps over the lazy dog. "
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
)

DEMO_SUMMARY = (
    "## Key Points\n\n"
    "- This is a simulated summary generated in demo mode\n"
    "- No real OpenAI API call was made\n"
    "- The transcript discussed various placeholder topics\n\n"
    "## Main Topics\n\n"
    "1. Quick brown fox athletics\n"
    "2. Lorem ipsum philosophy\n"
    "3. General placeholder discourse"
)

DEMO_DOWNLOAD_SECONDS = 5
DEMO_TRANSCRIBE_SECONDS = 5
DEMO_SUMMARIZE_SECONDS = 2
DEMO_MULTI_CHUNK_CHANCE = 0.5
DEMO_CHUNK_MARKER = "_chunk"
DEMO_PLACEHOLDER_AUDIO = b"\x00" * 1024


class DemoServices:
    """Fake download, chunking, transcription and summarization.

    Stateless, so one instance serves every request: how many chunks a run has is
    read back from the chunk files it wrote.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def download(self, url: str, accept: Callable[[float], None] | None = None) -> tuple[Path, float, str]:
        duration = random.randint(120, 7200)
        if accept is not None:
            accept(duration)
        tmp_dir = Path(self._settings.temp_dir)
        tmp_dir.mkdir(exist_ok=True)
        audio = tmp_dir / f"demo_{uuid.uuid4().hex[:8]}.{self._settings.audio_format}"
        audio.write_bytes(DEMO_PLACEHOLDER_AUDIO)
        await asyncio.sleep(DEMO_DOWNLOAD_SECONDS)
        return audio, duration, f"Demo: {url[:60]}"

    def prepare_chunks(self, audio: Path, duration_limit: int | None = None) -> list[Path]:
        if random.random() >= DEMO_MULTI_CHUNK_CHANCE:
            return [audio]
        chunks = [
            audio.with_name(f"{audio.stem}{DEMO_CHUNK_MARKER}{index}{audio.suffix}")
            for index in range(random.randint(2, 6))
        ]
        for chunk in chunks:
            chunk.write_bytes(DEMO_PLACEHOLDER_AUDIO)
        return chunks

    async def transcribe_chunk(self, chunk: Path, model: str) -> str:
        """Each chunk yields its share of the canned text, so the joined result reads whole."""
        index, count = self._position(chunk)
        await asyncio.sleep(DEMO_TRANSCRIBE_SECONDS / count)
        words = DEMO_TRANSCRIPT.split()
        per_chunk = -(-len(words) // count)
        return " ".join(words[index * per_chunk : (index + 1) * per_chunk])

    async def summarize(self, text: str, prompt: str, model: str) -> str:
        await asyncio.sleep(DEMO_SUMMARIZE_SECONDS)
        return DEMO_SUMMARY

    def _position(self, chunk: Path) -> tuple[int, int]:
        """(index of this chunk, how many chunks its run has). Unsplit audio is chunk 0 of 1."""
        base, marker, index = chunk.stem.rpartition(DEMO_CHUNK_MARKER)
        if not (marker and index.isdigit()):
            return 0, 1
        siblings = list(chunk.parent.glob(f"{base}{DEMO_CHUNK_MARKER}*{chunk.suffix}"))
        return int(index), len(siblings)
