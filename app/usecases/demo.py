"""Demo mode: the real use cases, run against simulated services.

Nothing here talks to YouTube or a model API. The transcription and summarization
flows are the production ones, so records, progress events, cancellation and
failure handling behave exactly as they do for real.
"""
import asyncio
import random
import uuid
from pathlib import Path

from app.config import settings
from app.usecases.transcription import Steps

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


def demo_steps() -> Steps:
    """Fake download, chunking and transcription for one run. Call once per request."""
    chunk_count = 1

    async def download(url: str) -> tuple[Path, float, str]:
        tmp_dir = Path(settings.temp_dir)
        tmp_dir.mkdir(exist_ok=True)
        audio = tmp_dir / f"demo_{uuid.uuid4().hex[:8]}.{settings.audio_format}"
        audio.write_bytes(b"\x00" * 1024)
        await asyncio.sleep(DEMO_DOWNLOAD_SECONDS)
        return audio, random.randint(120, 7200), f"Demo: {url[:60]}"

    def prepare_chunks(audio: Path, duration_limit: int | None) -> list[Path]:
        nonlocal chunk_count
        if random.random() >= DEMO_MULTI_CHUNK_CHANCE:
            return [audio]
        chunk_count = random.randint(2, 6)
        chunks = [audio.with_name(f"{audio.stem}{DEMO_CHUNK_MARKER}{i}{audio.suffix}") for i in range(chunk_count)]
        for chunk in chunks:
            chunk.write_bytes(b"\x00" * 1024)
        return chunks

    async def transcribe_chunk(chunk: Path, model: str) -> str:
        await asyncio.sleep(DEMO_TRANSCRIBE_SECONDS / chunk_count)
        # Each chunk yields its share of the canned text, so the joined result reads whole
        _, marker, index = chunk.stem.rpartition(DEMO_CHUNK_MARKER)
        i = int(index) if marker and index.isdigit() else 0
        words = DEMO_TRANSCRIPT.split()
        per_chunk = -(-len(words) // chunk_count)
        return " ".join(words[i * per_chunk:(i + 1) * per_chunk])

    return Steps(download=download, prepare_chunks=prepare_chunks, transcribe_chunk=transcribe_chunk, cache_audio=False)


async def demo_summarize(text: str, prompt: str = "", model: str = "") -> str:
    await asyncio.sleep(DEMO_SUMMARIZE_SECONDS)
    return DEMO_SUMMARY
