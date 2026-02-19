import asyncio
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.downloader import download_audio, DownloadError
from app.history import (
    create_record, complete_record, fail_record,
    get_history, get_result_path, delete_record,
    get_record_status, RESULTS_DIR,
)
from app.transcriber import prepare_chunks, transcribe_chunk, cleanup_temp_files

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
MAX_DURATION_SECONDS = 4 * 60 * 60  # 4 hours


class TranscribeRequest(BaseModel):
    url: str
    model: str = ""

    @field_validator("url")
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError("URL must be a YouTube link")
        return v


@router.post("/api/transcribe")
async def transcribe(req: TranscribeRequest, request: Request):
    async def event_generator():
        audio_path = None
        record_id = None
        try:
            logger.info("Request: %s", req.url)

            # Download
            yield {"event": "progress", "data": json.dumps({"stage": "downloading", "message": "Downloading audio from YouTube..."})}
            audio_path, duration, title = await download_audio(req.url)
            file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
            yield {"event": "progress", "data": json.dumps({"stage": "downloading", "message": f"Download complete ({file_size_mb:.1f} MB)"})}

            # Guard: max duration
            if duration and duration > MAX_DURATION_SECONDS:
                msg = f"Video too long ({duration // 3600}h {(duration % 3600) // 60}m). Max is 4 hours."
                logger.warning("Duration guard: %s", msg)
                yield {"event": "error", "data": json.dumps({"message": msg})}
                return

            # Guard: client disconnect
            if await request.is_disconnected():
                logger.warning("Client disconnected after download")
                return

            # Create history record
            record_id = create_record(title, req.url, duration)
            yield {"event": "progress", "data": json.dumps({"stage": "processing", "message": "Processing...", "record_id": record_id})}

            # Chunk (blocking I/O → thread pool)
            loop = asyncio.get_running_loop()
            chunks = await loop.run_in_executor(None, prepare_chunks, audio_path)
            if len(chunks) > 1:
                yield {"event": "progress", "data": json.dumps({"stage": "transcribing", "message": f"Audio split into {len(chunks)} chunks", "record_id": record_id})}

            # Transcribe
            transcript_parts = []
            for i, chunk_path in enumerate(chunks):
                if await request.is_disconnected():
                    logger.warning("Client disconnected during transcription")
                    break
                yield {"event": "progress", "data": json.dumps({"stage": "transcribing", "message": f"Transcribing{f' chunk {i+1} of {len(chunks)}' if len(chunks) > 1 else ''}...", "record_id": record_id})}
                text = await transcribe_chunk(chunk_path, model=req.model or None)
                transcript_parts.append(text)

            # Guard: don't save partial transcript if client disconnected
            if await request.is_disconnected():
                logger.warning("Client disconnected, leaving record as in_progress")
                return

            full_text = " ".join(transcript_parts)
            if not complete_record(record_id, full_text):
                logger.warning("Transcription succeeded but history write failed for %s", record_id)
            logger.info("Transcription done: %s", record_id)
            yield {"event": "transcript", "data": json.dumps({"text": full_text, "duration_seconds": duration, "title": title, "record_id": record_id})}
            yield {"event": "done", "data": "{}"}

        except DownloadError as e:
            logger.error("Download error: %s", e)
            if record_id:
                fail_record(record_id, str(e))
            yield {"event": "error", "data": json.dumps({"message": str(e), "record_id": record_id})}
        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            if record_id:
                fail_record(record_id, str(e))
            yield {"event": "error", "data": json.dumps({"message": f"An error occurred: {e}", "record_id": record_id})}
        finally:
            if record_id and get_record_status(record_id) == "in_progress":
                fail_record(record_id, "Transcription interrupted")
                logger.warning("Marked interrupted record as failed: %s", record_id)
            if audio_path:
                cleanup_temp_files(audio_path)

    return EventSourceResponse(event_generator())


@router.get("/api/history")
async def history():
    records = get_history()
    return records


@router.post("/api/history/{record_id}/reveal")
async def reveal_in_finder(record_id: str):
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    path = get_result_path(record_id)
    if not path:
        return JSONResponse({"error": "Not found"}, status_code=404)
    logger.info("Reveal in Finder: %s", record_id)
    subprocess.Popen(["open", "-R", str(path)])
    return {"ok": True}


@router.delete("/api/history/{record_id}")
async def delete_history(record_id: str):
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    if delete_record(record_id):
        return {"ok": True}
    return JSONResponse({"error": "Not found"}, status_code=404)


STALE_THRESHOLD_SECONDS = 10 * 60  # 10 minutes


@router.post("/api/cleanup")
async def cleanup():
    """Delete all temp files and clean up stale in_progress records."""
    deleted_files = 0
    cleaned_records = 0

    # Delete files in tmp/ (not recursive into subdirectories)
    temp_dir = Path(settings.temp_dir)
    if temp_dir.is_dir():
        for f in temp_dir.iterdir():
            if f.is_file() and f.resolve().parent == temp_dir.resolve():
                f.unlink(missing_ok=True)
                deleted_files += 1

    # Clean up stale in_progress records older than 10 minutes
    now = time.time()
    for record in get_history():
        if record["status"] != "in_progress":
            continue
        path = get_result_path(record["id"])
        if not path:
            continue
        age = now - path.stat().st_mtime
        if age > STALE_THRESHOLD_SECONDS:
            if fail_record(record["id"], "Cleaned up stale record"):
                cleaned_records += 1

    logger.info("Cleanup: deleted %d temp files, cleaned %d stale records", deleted_files, cleaned_records)
    return {"deleted_files": deleted_files, "cleaned_records": cleaned_records}
