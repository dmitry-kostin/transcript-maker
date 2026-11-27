"""Demo routes (`/api/demo/...`): the same handlers as the real ones, wired to simulated services."""
from fastapi import APIRouter, Request

from app.usecases.demo import demo_steps, demo_summarize
from app.web.schemas import DemoTranscribeRequest, RetranscribeRequest, SummarizeRequest
from app.web.shared import load_record_for_retranscribe, summarize, transcription_stream

router = APIRouter(prefix="/api/demo")


@router.post("/transcribe")
async def demo_transcribe(req: DemoTranscribeRequest, request: Request):
    return transcription_stream(request, req.url, req, steps=demo_steps())


@router.post("/history/{record_id}/retranscribe")
async def demo_retranscribe(record_id: str, req: RetranscribeRequest, request: Request):
    record = load_record_for_retranscribe(record_id)
    return transcription_stream(request, record["url"], req, record=record, steps=demo_steps())


@router.post("/history/{record_id}/summarize")
async def demo_summarize_history_record(record_id: str, req: SummarizeRequest):
    return await summarize(record_id, req, summarizer=demo_summarize)
