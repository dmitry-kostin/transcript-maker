"""HTTP routes. Validation, status codes and SSE only — the work happens in ``app.usecases``."""
import logging
import subprocess

from fastapi import APIRouter, Request

from app.domain.model_policy import available_providers
from app.storage.history import delete_record, get_history, get_result_path, get_summary
from app.usecases.maintenance import clean_up
from app.web.schemas import RetranscribeRequest, SummarizeRequest, TranscribeRequest
from app.web.shared import (
    ApiError, load_record, load_record_for_retranscribe, require_valid_id, summarize, transcription_stream,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/providers")
async def get_providers():
    """Return available model providers based on configured API keys."""
    return {"providers": available_providers()}


@router.post("/api/transcribe")
async def transcribe(req: TranscribeRequest, request: Request):
    return transcription_stream(request, req.url, req)


@router.get("/api/history")
async def history():
    return get_history()


@router.get("/api/history/{record_id}")
async def get_history_record(record_id: str):
    record = load_record(record_id)
    record.pop("path", None)
    return record


@router.post("/api/history/{record_id}/retranscribe")
async def retranscribe(record_id: str, req: RetranscribeRequest, request: Request):
    record = load_record_for_retranscribe(record_id)
    return transcription_stream(request, record["url"], req, record=record)


@router.post("/api/history/{record_id}/reveal")
async def reveal_in_finder(record_id: str):
    require_valid_id(record_id)
    path = get_result_path(record_id)
    if not path:
        raise ApiError(404, "Not found")
    logger.info("Reveal in Finder: %s", record_id)
    subprocess.Popen(["open", "-R", str(path)])
    return {"ok": True}


@router.post("/api/history/{record_id}/summarize")
async def summarize_history_record(record_id: str, req: SummarizeRequest):
    return await summarize(record_id, req)


@router.get("/api/history/{record_id}/summary")
async def get_record_summary(record_id: str):
    require_valid_id(record_id)
    result = get_summary(record_id)
    if not result:
        raise ApiError(404, "No summary found")
    return result


@router.delete("/api/history/{record_id}")
async def delete_history(record_id: str):
    require_valid_id(record_id)
    if not delete_record(record_id):
        raise ApiError(404, "Not found")
    return {"ok": True}


@router.post("/api/cleanup")
async def cleanup():
    """Fail stale in_progress records and delete leftover temp files."""
    return clean_up()
