"""The transcription use case: audio → chunks → text → saved record.

Knows nothing about HTTP. It yields ``(event_name, payload)`` tuples and asks the
caller whether to stop via ``is_cancelled``; the web layer turns those into SSE.
New transcriptions and re-transcriptions of an existing record share this one flow,
and so does demo mode — it runs the same flow with fake ``Steps``.
"""
import asyncio
import logging
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.domain.limits import too_long_message
from app.domain.model_policy import resolve_model
from app.services.downloader import DownloadError, download_audio
from app.services.transcriber import cleanup_temp_files, prepare_chunks, transcribe_chunk
from app.storage.history import (
    STATUS_IN_PROGRESS,
    complete_record, create_record, delete_chunk_cache, fail_record, find_cached_audio_by_url,
    get_audio_path, get_record_status, load_chunk_cache, reset_record, save_audio, save_chunk_cache,
)

logger = logging.getLogger(__name__)

Event = tuple[str, dict]


@dataclass(frozen=True)
class Steps:
    """The outside-world work the flow depends on. Swap these to run it without real services."""

    download: Callable[[str], Awaitable[tuple[Path, float, str]]]  # url → (audio, duration, title)
    prepare_chunks: Callable[[Path, int | None], list[Path]]  # (audio, duration_limit) → chunks; blocking
    transcribe_chunk: Callable[..., Awaitable[str]]  # (chunk, model=...) → text
    # Keep audio in results/ and reuse it for the same URL. Off for fakes, so
    # placeholder audio is never picked up by a real transcription later.
    cache_audio: bool = True


def real_steps() -> Steps:
    return Steps(
        download=download_audio,
        prepare_chunks=lambda audio, duration_limit: prepare_chunks(audio, duration_limit=duration_limit),
        transcribe_chunk=transcribe_chunk,
    )


@dataclass
class _Job:
    """What one run knows so far. Filled in step by step."""

    url: str
    model: str
    duration_limit: int
    is_cancelled: Callable[[], Awaitable[bool]]
    steps: Steps
    record: dict | None  # the record being re-transcribed, if any
    record_id: str | None
    owns_record: bool = False  # True once this run has put the record in progress
    audio_path: Path | None = None
    audio_needs_caching: bool = False
    title: str = ""
    duration: float = 0
    parts: list[str] = field(default_factory=list)


class _Cancelled(Exception):
    """The caller went away; stop without saving."""


def with_title(title: str, text: str) -> str:
    """A stored transcript always starts with the video title."""
    return f"{title}\n\n{text}"


def _progress(stage: str, message: str, **fields: object) -> Event:
    return "progress", {"stage": stage, "message": message, **fields}


def _error(message: str, record_id: str | None = None) -> Event:
    return "error", {"message": message, "record_id": record_id}


async def _in_thread(fn: Callable, *args: object) -> object:
    """Run blocking file or ffmpeg work off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


async def _stop_if_cancelled(job: _Job, where: str) -> None:
    if await job.is_cancelled():
        logger.warning("Client disconnected %s%s", where, f" for {job.record_id}" if job.record_id else "")
        raise _Cancelled


async def _copy_to_temp(source: Path, prefix: str) -> Path:
    """Copy cached audio into tmp/ so chunking never touches the cached original."""
    tmp_dir = Path(settings.temp_dir)
    tmp_dir.mkdir(exist_ok=True)
    target = tmp_dir / f"{prefix}_{uuid.uuid4().hex[:8]}{source.suffix}"
    await _in_thread(shutil.copy2, source, target)
    return target


async def _acquire_audio(job: _Job) -> AsyncIterator[Event]:
    """Put the audio in tmp/ — from the cache when there is one, otherwise by downloading."""
    record = job.record
    cached_audio: Path | None = None
    if record:
        job.title, job.duration = record["title"], record["duration"]
        if job.steps.cache_audio:
            cached_audio = get_audio_path(record["id"])
    elif job.steps.cache_audio:
        cached = find_cached_audio_by_url(job.url)
        if cached:
            cached_audio, source_record = cached
            job.title, job.duration = source_record["title"], source_record["duration"]

    if cached_audio:
        yield _progress("downloading", "Using cached audio...")
        job.audio_path = await _copy_to_temp(cached_audio, f"retranscribe_{record['id']}" if record else "cached")
        # A new record gets its own copy; an existing one already has it
        job.audio_needs_caching = job.steps.cache_audio and not record
        return

    yield _progress("downloading", "Downloading audio from YouTube...")
    job.audio_path, duration, title = await job.steps.download(job.url)
    file_size_mb = job.audio_path.stat().st_size / (1024 * 1024)
    yield _progress("downloading", f"Download complete ({file_size_mb:.1f} MB)")
    job.audio_needs_caching = job.steps.cache_audio
    if not record:
        job.title, job.duration = title, duration


def _open_record(job: _Job) -> bool:
    """Put the record in progress: reset the existing one or create a new one.

    False means the existing record could not be taken — another run has it, or it is gone.
    """
    if job.record:
        if not reset_record(job.record_id, model=job.model, duration_limit=job.duration_limit):
            return False
    else:
        job.record_id = create_record(
            job.title, job.url, job.duration, model=job.model, duration_limit=job.duration_limit,
        )
    job.owns_record = True
    return True


async def _transcribe_chunks(job: _Job, chunks: list[Path]) -> AsyncIterator[Event]:
    """Transcribe each chunk into ``job.parts``, resuming from the chunk cache."""
    record_id, model, total = job.record_id, job.model, len(chunks)
    cached_parts = load_chunk_cache(record_id, model, total)
    job.parts = list(cached_parts)
    if cached_parts:
        logger.info("Loaded %d cached chunks for %s", len(cached_parts), record_id)

    chunk_times: list[float] = []
    for i, chunk_path in enumerate(chunks):
        number = i + 1
        position = {"record_id": record_id, "chunk": number, "chunks_total": total}
        if i < len(cached_parts):
            logger.info("Chunk %d/%d cached (%d words), skipping", number, total, len(cached_parts[i].split()))
            if total > 1:
                yield _progress("transcribing", f"Chunk {number} of {total} (cached)", **position)
            continue

        await _stop_if_cancelled(job, f"before chunk {number}/{total}")
        if chunk_times and total > 1:
            # ETA from the rolling average of the chunks done so far
            position["eta_seconds"] = round(sum(chunk_times) / len(chunk_times) * (total - i))
        label = f" chunk {number} of {total}" if total > 1 else ""
        yield _progress("transcribing", f"Transcribing{label}...", **position)

        chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)
        logger.info("Transcribing chunk %d/%d (%.1f MB) for %s", number, total, chunk_size_mb, record_id)
        started = time.monotonic()
        text = await job.steps.transcribe_chunk(chunk_path, model=model)
        elapsed = time.monotonic() - started
        chunk_times.append(elapsed)
        logger.info("Chunk %d/%d done: %d words, %.1fs, %.1f MB for %s", number, total, len(text.split()), elapsed, chunk_size_mb, record_id)
        if not text.strip():
            logger.error("Chunk %d/%d returned empty text for %s, aborting", number, total, record_id)
            raise RuntimeError(f"Chunk {number} of {total} returned empty transcription")
        job.parts.append(text)
        save_chunk_cache(record_id, model, total, job.parts)


async def run_transcription(
    url: str,
    requested_model: str,
    duration_limit: int,
    is_cancelled: Callable[[], Awaitable[bool]],
    record: dict | None = None,
    steps: Steps | None = None,
) -> AsyncIterator[Event]:
    """Transcribe ``url`` and save the result, yielding progress as it goes.

    ``duration_limit`` is in seconds (0 = no limit). Pass ``record`` to re-transcribe
    an existing record in place; otherwise a new record is created after download.
    An existing record is only touched once this run has taken it: until then a
    failure leaves it exactly as it was, and afterwards its previous transcript
    stays in the file unless the new one completes.
    """
    job = _Job(
        url=url,
        model=resolve_model(requested_model),  # resolved once — stored and executed
        duration_limit=duration_limit,
        is_cancelled=is_cancelled,
        steps=steps or real_steps(),
        record=record,
        record_id=record["id"] if record else None,
    )

    try:
        logger.info("Request: %s%s", url, f" (retranscribe {job.record_id})" if record else "")

        async for event in _acquire_audio(job):
            yield event

        if not record:
            # The stored record's length was accepted when it was first transcribed
            too_long = too_long_message(job.duration, duration_limit)
            if too_long:
                logger.warning("Duration guard: %s", too_long)
                yield _error(too_long)
                return
        await _stop_if_cancelled(job, "after download")

        if not _open_record(job):
            yield _error("Record is being processed by another request or no longer exists", job.record_id)
            return
        if job.audio_needs_caching:
            # Cached from the local copy (safe even if the original is deleted concurrently)
            await _in_thread(save_audio, job.record_id, job.audio_path)
        yield _progress("processing", "Processing...", record_id=job.record_id)

        chunks = await _in_thread(job.steps.prepare_chunks, job.audio_path, duration_limit or None)
        if len(chunks) > 1:
            yield _progress("transcribing", f"Audio split into {len(chunks)} chunks", record_id=job.record_id)

        async for event in _transcribe_chunks(job, chunks):
            yield event
        # Don't save a transcript nobody is waiting for
        await _stop_if_cancelled(job, "after the last chunk, not saving")

        full_text = with_title(job.title, " ".join(job.parts))
        if not complete_record(job.record_id, full_text):
            logger.warning("Transcription succeeded but history write failed for %s", job.record_id)
        delete_chunk_cache(job.record_id)
        logger.info("Transcription done: %s (%d chunks, %d words saved)", job.record_id, len(job.parts), sum(len(p.split()) for p in job.parts))
        yield "transcript", {
            "text": full_text, "duration_seconds": job.duration, "duration_limit": duration_limit,
            "title": job.title, "record_id": job.record_id, "model": job.model,
        }
        yield "done", {}

    except _Cancelled:
        pass  # the finally below marks the record interrupted
    except DownloadError as e:
        logger.error("Download error: %s", e)
        if job.owns_record:
            fail_record(job.record_id, str(e))
        yield _error(str(e), job.record_id)
    except Exception as e:
        logger.error("Unexpected error: %s", e, exc_info=True)
        if job.owns_record:
            fail_record(job.record_id, str(e))
        yield _error(f"An error occurred: {e}", job.record_id)
    finally:
        if job.owns_record and get_record_status(job.record_id) == STATUS_IN_PROGRESS:
            fail_record(job.record_id, "Transcription interrupted")
            logger.warning("Marked interrupted record as failed: %s", job.record_id)
        if job.audio_path:
            cleanup_temp_files(job.audio_path)
