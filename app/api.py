import asyncio
import json
import logging
import random
import subprocess
from collections.abc import AsyncIterator
from urllib.parse import urlparse, parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from app.config import settings  # noqa: F401 — tests patch settings through this module
from app.history import (
    STATUS_DONE, STATUS_IN_PROGRESS,
    get_history, create_record, complete_record, fail_record,
    get_record_status, get_result_path, delete_record, get_record, reset_record,
    save_summary, get_summary, is_valid_record_id, fail_stale_records,
)
from app.model_policy import available_providers, resolve_model
from app.pipeline import Event, run_transcription, with_title
from app.summarizer import summarize_text
from app.transcriber import purge_temp_dir

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

DEMO_DOWNLOAD_SECONDS = 5
DEMO_TRANSCRIBE_SECONDS = 5
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


async def _demo_event_generator(url: str, request: Request, record_id: str | None = None, title: str | None = None, duration_limit: int = 0, model: str = ""):
    """Mock SSE generator — simulates download + transcribe with sleeps, no real APIs."""
    title = title or f"Demo: {url[:60]}"
    duration = random.randint(120, 7200)
    use_chunks = random.random() < 0.5
    num_chunks = random.randint(2, 6) if use_chunks else 1

    # Simulate duration limit clipping
    if duration_limit and duration > duration_limit:
        duration = duration_limit

    stored_model = resolve_model(model)

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
            reset_record(record_id, model=stored_model, duration_limit=duration_limit)
        else:
            record_id = create_record(title, url, duration, model=stored_model, duration_limit=duration_limit)

        yield {"event": "progress", "data": json.dumps({"stage": "processing", "message": "Processing...", "record_id": record_id})}
        await asyncio.sleep(1.0)

        if num_chunks > 1:
            yield {"event": "progress", "data": json.dumps({"stage": "transcribing", "message": f"Audio split into {num_chunks} chunks", "record_id": record_id, "chunk": 0, "chunks_total": num_chunks})}

        # Simulate transcription (10s total, split across chunks)
        steps_per_chunk = max(1, int(DEMO_TRANSCRIBE_SECONDS / DEMO_TICK / num_chunks))
        time_per_chunk = steps_per_chunk * DEMO_TICK
        for chunk_i in range(num_chunks):
            if await request.is_disconnected():
                return
            chunk_label = f" chunk {chunk_i + 1} of {num_chunks}" if num_chunks > 1 else ""
            progress_data: dict = {"stage": "transcribing", "message": f"Transcribing{chunk_label}...", "record_id": record_id, "chunk": chunk_i + 1, "chunks_total": num_chunks}
            if chunk_i >= 1:
                progress_data["eta_seconds"] = round(time_per_chunk * (num_chunks - chunk_i), 1)
            yield {"event": "progress", "data": json.dumps(progress_data)}
            for _ in range(steps_per_chunk):
                if await request.is_disconnected():
                    return
                await asyncio.sleep(DEMO_TICK)

        if await request.is_disconnected():
            return

        # Complete
        full_text = with_title(title, DEMO_TRANSCRIPT)
        complete_record(record_id, full_text)
        yield {"event": "transcript", "data": json.dumps({"text": full_text, "duration_seconds": duration, "duration_limit": duration_limit, "title": title, "record_id": record_id, "model": stored_model})}
        yield {"event": "done", "data": "{}"}

    except Exception as e:
        logger.error("Demo error: %s", e, exc_info=True)
        if record_id:
            fail_record(record_id, str(e))
        yield {"event": "error", "data": json.dumps({"message": f"Demo error: {e}", "record_id": record_id})}
    finally:
        if record_id and get_record_status(record_id) == STATUS_IN_PROGRESS:
            fail_record(record_id, "Transcription interrupted")


ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
MAX_DURATION_LIMIT_MINUTES = 480  # 8 hours — matches frontend max attribute


class TranscribeRequest(BaseModel):
    url: str
    model: str = ""
    duration_limit: int = 0  # minutes from frontend, converted to seconds in handler

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

    @field_validator("duration_limit")
    @classmethod
    def validate_duration_limit(cls, v: int) -> int:
        if v < 0:
            raise ValueError("duration_limit must be >= 0")
        if v > MAX_DURATION_LIMIT_MINUTES:
            raise ValueError(f"duration_limit must be <= {MAX_DURATION_LIMIT_MINUTES} minutes")
        return v


class RetranscribeRequest(BaseModel):
    model: str = ""
    duration_limit: int = 0  # minutes from frontend, converted to seconds in handler

    @field_validator("duration_limit")
    @classmethod
    def validate_duration_limit(cls, v: int) -> int:
        if v < 0:
            raise ValueError("duration_limit must be >= 0")
        if v > MAX_DURATION_LIMIT_MINUTES:
            raise ValueError(f"duration_limit must be <= {MAX_DURATION_LIMIT_MINUTES} minutes")
        return v


class SummarizeRequest(BaseModel):
    prompt: str = ""
    model: str = ""


@router.post("/api/demo/transcribe")
async def demo_transcribe(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    limit_min = int(body.get("duration_limit", 0) or 0)
    # duration_limit: minutes from client → seconds for storage
    limit_sec = limit_min * 60 if limit_min else 0
    return EventSourceResponse(_demo_event_generator(
        body.get("url", "demo"), request,
        duration_limit=limit_sec, model=body.get("model", ""),
    ))


@router.post("/api/demo/history/{record_id}/retranscribe")
async def demo_retranscribe(record_id: str, request: Request):
    if not is_valid_record_id(record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    body = await request.json()
    limit_min = int(body.get("duration_limit", 0) or 0)
    # duration_limit: minutes from client → seconds for storage (0 = no limit)
    limit_sec = limit_min * 60 if limit_min else 0
    return EventSourceResponse(_demo_event_generator(
        record["url"], request,
        record_id=record_id, title=record["title"],
        duration_limit=limit_sec, model=body.get("model", ""),
    ))


@router.post("/api/demo/history/{record_id}/summarize")
async def demo_summarize(record_id: str, req: SummarizeRequest):
    if not is_valid_record_id(record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if record["status"] != STATUS_DONE:
        return JSONResponse({"error": "Record is not completed"}, status_code=400)
    await asyncio.sleep(2)
    prompt = req.prompt.strip()
    summary_with_title = f"# {record['title']}\n\n{DEMO_SUMMARY}"
    if not save_summary(record_id, summary_with_title, prompt):
        return JSONResponse({"error": "Failed to save summary"}, status_code=500)
    return {"summary": summary_with_title, "prompt": prompt}


@router.get("/api/providers")
async def get_providers():
    """Return available model providers based on configured API keys."""
    return {"providers": available_providers()}


async def _as_sse(events: AsyncIterator[Event]) -> AsyncIterator[dict]:
    """Serialize pipeline events into SSE messages."""
    async for name, payload in events:
        yield {"event": name, "data": json.dumps(payload)}


@router.post("/api/transcribe")
async def transcribe(req: TranscribeRequest, request: Request):
    return EventSourceResponse(_as_sse(run_transcription(
        req.url, req.model,
        duration_limit=req.duration_limit * 60,  # minutes from client → seconds
        is_cancelled=request.is_disconnected,
    )))


@router.get("/api/history")
async def history():
    records = get_history()
    return records


@router.get("/api/history/{record_id}")
async def get_history_record(record_id: str):
    if not is_valid_record_id(record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    record.pop("path", None)
    return record


@router.post("/api/history/{record_id}/retranscribe")
async def retranscribe(record_id: str, req: RetranscribeRequest, request: Request):
    if not is_valid_record_id(record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if record["status"] == STATUS_IN_PROGRESS:
        return JSONResponse({"error": "Record is currently being processed"}, status_code=409)
    return EventSourceResponse(_as_sse(run_transcription(
        record["url"], req.model,
        duration_limit=req.duration_limit * 60,  # minutes from client → seconds
        is_cancelled=request.is_disconnected,
        record=record,
    )))


@router.post("/api/history/{record_id}/reveal")
async def reveal_in_finder(record_id: str):
    if not is_valid_record_id(record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    path = get_result_path(record_id)
    if not path:
        return JSONResponse({"error": "Not found"}, status_code=404)
    logger.info("Reveal in Finder: %s", record_id)
    subprocess.Popen(["open", "-R", str(path)])
    return {"ok": True}


@router.post("/api/history/{record_id}/summarize")
async def summarize(record_id: str, req: SummarizeRequest):
    if not is_valid_record_id(record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    record = get_record(record_id)
    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if record["status"] != STATUS_DONE:
        return JSONResponse({"error": "Record is not completed"}, status_code=400)
    try:
        summary = await summarize_text(record["body"], req.prompt, model=req.model)
    except Exception as e:
        logger.error("Summarize error for %s: %s", record_id, e, exc_info=True)
        return JSONResponse({"error": f"Summarization failed: {e}"}, status_code=500)
    prompt = req.prompt.strip()
    summary_with_title = f"# {record['title']}\n\n{summary}"
    if not save_summary(record_id, summary_with_title, prompt):
        return JSONResponse({"error": "Failed to save summary"}, status_code=500)
    return {"summary": summary_with_title, "prompt": prompt}


@router.get("/api/history/{record_id}/summary")
async def get_record_summary(record_id: str):
    if not is_valid_record_id(record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    result = get_summary(record_id)
    if not result:
        return JSONResponse({"error": "No summary found"}, status_code=404)
    return result


@router.delete("/api/history/{record_id}")
async def delete_history(record_id: str):
    if not is_valid_record_id(record_id):
        return JSONResponse({"error": "Invalid ID"}, status_code=400)
    if delete_record(record_id):
        return {"ok": True}
    return JSONResponse({"error": "Not found"}, status_code=404)


STALE_THRESHOLD_SECONDS = 10 * 60  # 10 minutes


@router.post("/api/cleanup")
async def cleanup():
    """Delete all temp files and clean up stale in_progress records."""
    deleted_files = purge_temp_dir()
    cleaned_records = fail_stale_records("Cleaned up stale record", older_than_seconds=STALE_THRESHOLD_SECONDS)
    logger.info("Cleanup: deleted %d temp files, cleaned %d stale records", deleted_files, cleaned_records)
    return {"deleted_files": deleted_files, "cleaned_records": cleaned_records}
