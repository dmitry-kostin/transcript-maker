import asyncio
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.downloader import download_audio, DownloadError
from app.history import (
    create_record, complete_record, fail_record,
    get_history, get_result_path, delete_record,
    get_record, get_record_status, reset_record,
    save_audio, get_audio_path, RESULTS_DIR,
    save_summary, get_summary,
)
from app.transcriber import prepare_chunks, transcribe_chunk, cleanup_temp_files
from app.summarizer import summarize_text

logger = logging.getLogger(__name__)
router = APIRouter()

# ─── Demo / mock mode ───

DEMO_TRANSCRIPT = (
    "This is a simulated transcript generated in demo mode. "
    "No real YouTube download or OpenAI API call was made. "
    "The quick brown fox jumps over the lazy dog. "
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
)

DEMO_DOWNLOAD_SECONDS = 10
DEMO_TRANSCRIBE_SECONDS = 10
DEMO_TICK = 0.5

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


async def _demo_event_generator(url: str, model: str, request: Request, record_id: str | None = None, title: str | None = None):
    """Mock SSE generator — simulates download + transcribe with sleeps, no real APIs."""
    title = title or f"Demo: {url[:60]}"
    duration = random.randint(120, 7200)
    use_chunks = random.random() < 0.5
    num_chunks = random.randint(2, 6) if use_chunks else 1

    try:
        # Simulate download (10s)
        steps = int(DEMO_DOWNLOAD_SECONDS / DEMO_TICK)
        for i in range(steps):
            if await request.is_disconnected():
                return
            pct = int((i + 1) / steps * 100)
            yield {"event": "progress", "data": json.dumps({"stage": "downloading", "message": f"Downloading audio... {pct}%"})}
            await asyncio.sleep(DEMO_TICK)

        yield {"event": "progress", "data": json.dumps({"stage": "downloading", "message": "Download complete (42.0 MB)"})}

        if await request.is_disconnected():
            return

        # Create or reset record
        if record_id:
            reset_record(record_id, model=model)
        else:
            record_id = create_record(title, url, duration, model=model)

        yield {"event": "progress", "data": json.dumps({"stage": "processing", "message": "Processing...", "record_id": record_id})}
        await asyncio.sleep(1.0)

        if num_chunks > 1:
            yield {"event": "progress", "data": json.dumps({"stage": "transcribing", "message": f"Audio split into {num_chunks} chunks", "record_id": record_id})}

        # Simulate transcription (10s total, split across chunks)
        steps_per_chunk = max(1, int(DEMO_TRANSCRIBE_SECONDS / DEMO_TICK / num_chunks))
        for chunk_i in range(num_chunks):
            if await request.is_disconnected():
                return
            chunk_label = f" chunk {chunk_i + 1} of {num_chunks}" if num_chunks > 1 else ""
            yield {"event": "progress", "data": json.dumps({"stage": "transcribing", "message": f"Transcribing{chunk_label}...", "record_id": record_id})}
            if num_chunks > 1:
                yield {"event": "chunk_progress", "data": json.dumps({"current": chunk_i, "total": num_chunks})}
            for _ in range(steps_per_chunk):
                if await request.is_disconnected():
                    return
                await asyncio.sleep(DEMO_TICK)

        if await request.is_disconnected():
            return

        # Complete
        full_text = DEMO_TRANSCRIPT
        if model == "gpt-4o-transcribe-diarize":
            full_text = "Speaker 1: " + DEMO_TRANSCRIPT
        full_text = f"{title}\n\n{full_text}"
        complete_record(record_id, full_text)
        yield {"event": "transcript", "data": json.dumps({"text": full_text, "duration_seconds": duration, "title": title, "record_id": record_id})}
        yield {"event": "done", "data": "{}"}

    except Exception as e:
        logger.error("Demo error: %s", e, exc_info=True)
        if record_id:
            fail_record(record_id, str(e))
        yield {"event": "error", "data": json.dumps({"message": f"Demo error: {e}", "record_id": record_id})}
    finally:
        if record_id and get_record_status(record_id) == "in_progress":
            fail_record(record_id, "Transcription interrupted")

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
        qs = parse_qs(parsed.query)
        has_video_id = "v" in qs or (
            parsed.hostname == "youtu.be" and len(parsed.path) > 1
        )
        if parsed.path == "/playlist" or ("list" in qs and not has_video_id):
            raise ValueError(
                "Playlist URLs are not supported. Please provide a single video URL."
            )
        return v


class RetranscribeRequest(BaseModel):
    model: str = ""


class SummarizeRequest(BaseModel):
    prompt: str = ""


@router.post("/api/demo/transcribe")
async def demo_transcribe(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return EventSourceResponse(_demo_event_generator(body.get("url", "demo"), body.get("model", ""), request))


@router.post("/api/demo/history/{record_id}/retranscribe")
async def demo_retranscribe(record_id: str, request: Request):
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    body = await request.json()
    return EventSourceResponse(_demo_event_generator(
        record["url"], body.get("model", ""), request,
        record_id=record_id, title=record["title"],
    ))


@router.post("/api/demo/history/{record_id}/summarize")
async def demo_summarize(record_id: str, req: SummarizeRequest):
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if record["status"] != "done":
        return JSONResponse({"error": "Record is not completed"}, status_code=400)
    await asyncio.sleep(2)
    prompt = req.prompt.strip()
    summary_with_title = f"{record['title']}\n\n{DEMO_SUMMARY}"
    save_summary(record_id, summary_with_title, prompt)
    return {"summary": summary_with_title, "prompt": prompt}


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
            record_id = create_record(title, req.url, duration, model=req.model)
            save_audio(record_id, audio_path)
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

            full_text = f"{title}\n\n{' '.join(transcript_parts)}"
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


@router.get("/api/history/{record_id}")
async def get_history_record(record_id: str):
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    record.pop("path", None)
    return record


@router.post("/api/history/{record_id}/retranscribe")
async def retranscribe(record_id: str, req: RetranscribeRequest, request: Request):
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if record["status"] == "in_progress":
        return JSONResponse({"error": "Record is currently being processed"}, status_code=409)

    url = record["url"]

    async def event_generator():
        audio_path = None
        try:
            logger.info("Retranscribe request: %s (record %s)", url, record_id)

            # Try cached audio first, fall back to re-download
            cached_audio = get_audio_path(record_id)
            if cached_audio:
                yield {"event": "progress", "data": json.dumps({"stage": "downloading", "message": "Using cached audio..."})}
                tmp_dir = Path(settings.temp_dir)
                tmp_dir.mkdir(exist_ok=True)
                tmp_name = f"retranscribe_{record_id}_{uuid.uuid4().hex[:8]}{cached_audio.suffix}"
                audio_path = tmp_dir / tmp_name
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, shutil.copy2, cached_audio, audio_path)
            else:
                yield {"event": "progress", "data": json.dumps({"stage": "downloading", "message": "Downloading audio from YouTube..."})}
                audio_path, _duration, _title = await download_audio(url)
                file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
                yield {"event": "progress", "data": json.dumps({"stage": "downloading", "message": f"Download complete ({file_size_mb:.1f} MB)"})}
                save_audio(record_id, audio_path)

            # Guard: client disconnect
            if await request.is_disconnected():
                logger.warning("Client disconnected after download")
                return

            # Reset the record to in_progress with the new model
            reset_record(record_id, model=req.model)
            yield {"event": "progress", "data": json.dumps({"stage": "processing", "message": "Processing...", "record_id": record_id})}

            # Chunk
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

            full_text = f"{record['title']}\n\n{' '.join(transcript_parts)}"
            if not complete_record(record_id, full_text):
                logger.warning("Retranscription succeeded but history write failed for %s", record_id)
            logger.info("Retranscription done: %s", record_id)
            yield {"event": "transcript", "data": json.dumps({"text": full_text, "duration_seconds": record["duration"], "title": record["title"], "record_id": record_id})}
            yield {"event": "done", "data": "{}"}

        except DownloadError as e:
            logger.error("Download error: %s", e)
            fail_record(record_id, str(e))
            yield {"event": "error", "data": json.dumps({"message": str(e), "record_id": record_id})}
        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            fail_record(record_id, str(e))
            yield {"event": "error", "data": json.dumps({"message": f"An error occurred: {e}", "record_id": record_id})}
        finally:
            if get_record_status(record_id) == "in_progress":
                fail_record(record_id, "Transcription interrupted")
                logger.warning("Marked interrupted record as failed: %s", record_id)
            if audio_path:
                cleanup_temp_files(audio_path)

    return EventSourceResponse(event_generator())


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


@router.post("/api/history/{record_id}/summarize")
async def summarize(record_id: str, req: SummarizeRequest):
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if record["status"] != "done":
        return JSONResponse({"error": "Record is not completed"}, status_code=400)
    try:
        summary = await summarize_text(record["body"], req.prompt)
    except Exception as e:
        logger.error("Summarize error for %s: %s", record_id, e, exc_info=True)
        return JSONResponse({"error": f"Summarization failed: {e}"}, status_code=500)
    prompt = req.prompt.strip()
    summary_with_title = f"{record['title']}\n\n{summary}"
    save_summary(record_id, summary_with_title, prompt)
    return {"summary": summary_with_title, "prompt": prompt}


@router.get("/api/history/{record_id}/summary")
async def get_record_summary(record_id: str):
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    result = get_summary(record_id)
    if not result:
        return JSONResponse({"error": "No summary found"}, status_code=404)
    return result


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
