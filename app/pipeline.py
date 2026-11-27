"""The transcription use case: audio → chunks → text → saved record.

Knows nothing about HTTP. It yields ``(event_name, payload)`` tuples and asks the
caller whether to stop via ``is_cancelled``; ``api.py`` turns those into SSE.
New transcriptions and re-transcriptions of an existing record share this one flow.
"""
import asyncio
import logging
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from app.config import settings
from app.downloader import DownloadError, download_audio
from app.history import (
    STATUS_IN_PROGRESS,
    complete_record, create_record, delete_chunk_cache, fail_record, find_cached_audio_by_url,
    get_audio_path, get_record_status, load_chunk_cache, reset_record, save_audio, save_chunk_cache,
)
from app.model_policy import resolve_model
from app.transcriber import cleanup_temp_files, prepare_chunks, transcribe_chunk

logger = logging.getLogger(__name__)

MAX_DURATION_SECONDS = 4 * 60 * 60  # 4 hours

Event = tuple[str, dict]


def with_title(title: str, text: str) -> str:
    """A stored transcript always starts with the video title."""
    return f"{title}\n\n{text}"


async def _copy_to_temp(source: Path, prefix: str) -> Path:
    """Copy cached audio into tmp/ so chunking never touches the cached original."""
    tmp_dir = Path(settings.temp_dir)
    tmp_dir.mkdir(exist_ok=True)
    target = tmp_dir / f"{prefix}_{uuid.uuid4().hex[:8]}{source.suffix}"
    await asyncio.get_running_loop().run_in_executor(None, shutil.copy2, source, target)
    return target


def _progress(stage: str, message: str, **fields: object) -> Event:
    return "progress", {"stage": stage, "message": message, **fields}


async def run_transcription(
    url: str,
    requested_model: str,
    duration_limit: int,
    is_cancelled: Callable[[], Awaitable[bool]],
    record: dict | None = None,
) -> AsyncIterator[Event]:
    """Transcribe ``url`` and save the result, yielding progress as it goes.

    ``duration_limit`` is in seconds (0 = no limit). Pass ``record`` to re-transcribe
    an existing record in place; otherwise a new record is created after download.
    """
    # Resolve model once — used for both storage and execution
    model = resolve_model(requested_model)
    record_id: str | None = record["id"] if record else None
    audio_path: Path | None = None

    try:
        logger.info("Request: %s%s", url, f" (retranscribe {record_id})" if record else "")

        # Audio: reuse the cached copy when there is one, otherwise download
        if record:
            cached_audio = get_audio_path(record["id"])
            title, duration = record["title"], record["duration"]
        else:
            cached = find_cached_audio_by_url(url)
            cached_audio = cached[0] if cached else None
            title, duration = (cached[1]["title"], cached[1]["duration"]) if cached else ("", 0)

        if cached_audio:
            yield _progress("downloading", "Using cached audio...")
            audio_path = await _copy_to_temp(cached_audio, f"retranscribe_{record_id}" if record else "cached")
        else:
            yield _progress("downloading", "Downloading audio from YouTube...")
            audio_path, downloaded_duration, downloaded_title = await download_audio(url)
            file_size_mb = audio_path.stat().st_size / (1024 * 1024)
            yield _progress("downloading", f"Download complete ({file_size_mb:.1f} MB)")
            if record:
                save_audio(record["id"], audio_path)
            else:
                title, duration = downloaded_title, downloaded_duration

        if not record and duration and duration > MAX_DURATION_SECONDS:
            msg = f"Video too long ({duration // 3600}h {(duration % 3600) // 60}m). Max is 4 hours."
            logger.warning("Duration guard: %s", msg)
            yield "error", {"message": msg}
            return

        if await is_cancelled():
            logger.warning("Client disconnected after download")
            return

        # Record: reset the existing one, or create it and cache the audio beside it
        if record:
            reset_record(record["id"], model=model, duration_limit=duration_limit)
        else:
            record_id = create_record(title, url, duration, model=model, duration_limit=duration_limit)
            # Cache audio from the local copy (safe even if original is deleted concurrently)
            save_audio(record_id, audio_path)
        yield _progress("processing", "Processing...", record_id=record_id)

        # Chunk (blocking I/O → thread pool)
        source = audio_path
        chunks = await asyncio.get_running_loop().run_in_executor(
            None, lambda: prepare_chunks(source, duration_limit=duration_limit or None)
        )
        total = len(chunks)
        if total > 1:
            yield _progress("transcribing", f"Audio split into {total} chunks", record_id=record_id)

        # Transcribe (with chunk cache for resume)
        cached_parts = load_chunk_cache(record_id, model, total)
        parts = list(cached_parts)
        if cached_parts:
            logger.info("Loaded %d cached chunks for %s", len(cached_parts), record_id)
        chunk_times: list[float] = []
        for i, chunk_path in enumerate(chunks):
            position = {"record_id": record_id, "chunk": i + 1, "chunks_total": total}
            if i < len(cached_parts):
                logger.info("Chunk %d/%d cached (%d words), skipping", i + 1, total, len(cached_parts[i].split()))
                if total > 1:
                    yield _progress("transcribing", f"Chunk {i + 1} of {total} (cached)", **position)
                continue
            if await is_cancelled():
                logger.warning("Client disconnected before chunk %d/%d for %s", i + 1, total, record_id)
                break
            if chunk_times and total > 1:
                # ETA from the rolling average of the chunks done so far
                position["eta_seconds"] = round(sum(chunk_times) / len(chunk_times) * (total - i))
            label = f" chunk {i + 1} of {total}" if total > 1 else ""
            yield _progress("transcribing", f"Transcribing{label}...", **position)

            chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)
            logger.info("Transcribing chunk %d/%d (%.1f MB) for %s", i + 1, total, chunk_size_mb, record_id)
            started = time.monotonic()
            text = await transcribe_chunk(chunk_path, model=model)
            elapsed = time.monotonic() - started
            chunk_times.append(elapsed)
            logger.info("Chunk %d/%d done: %d words, %.1fs, %.1f MB for %s", i + 1, total, len(text.split()), elapsed, chunk_size_mb, record_id)
            if not text.strip():
                logger.error("Chunk %d/%d returned empty text for %s, aborting", i + 1, total, record_id)
                raise RuntimeError(f"Chunk {i + 1} of {total} returned empty transcription")
            parts.append(text)
            save_chunk_cache(record_id, model, total, parts)

        # Guard: don't save partial transcript if client disconnected
        if await is_cancelled():
            logger.warning("Client disconnected after chunk loop for %s, not saving", record_id)
            return

        full_text = with_title(title, " ".join(parts))
        if not complete_record(record_id, full_text):
            logger.warning("Transcription succeeded but history write failed for %s", record_id)
        delete_chunk_cache(record_id)
        logger.info("Transcription done: %s (%d chunks, %d words saved)", record_id, len(parts), sum(len(p.split()) for p in parts))
        yield "transcript", {
            "text": full_text, "duration_seconds": duration, "duration_limit": duration_limit,
            "title": title, "record_id": record_id, "model": model,
        }
        yield "done", {}

    except DownloadError as e:
        logger.error("Download error: %s", e)
        if record_id:
            fail_record(record_id, str(e))
        yield "error", {"message": str(e), "record_id": record_id}
    except Exception as e:
        logger.error("Unexpected error: %s", e, exc_info=True)
        if record_id:
            fail_record(record_id, str(e))
        yield "error", {"message": f"An error occurred: {e}", "record_id": record_id}
    finally:
        if record_id and get_record_status(record_id) == STATUS_IN_PROGRESS:
            fail_record(record_id, "Transcription interrupted")
            logger.warning("Marked interrupted record as failed: %s", record_id)
        if audio_path:
            cleanup_temp_files(audio_path)
