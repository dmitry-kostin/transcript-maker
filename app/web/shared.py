"""What the real and the demo routes have in common: record lookup, error shape, SSE, and the calls into the use cases."""
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.storage.history import STATUS_IN_PROGRESS, get_record, is_valid_record_id
from app.usecases.summarization import RecordNotCompleted, SummaryNotSaved, summarize_record
from app.usecases.transcription import Event, Steps, run_transcription
from app.web.schemas import SummarizeRequest, TranscriptionOptions

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """An error the client should see as ``{"error": message}`` with this status code."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


async def api_error_response(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status_code)


def require_valid_id(record_id: str) -> None:
    if not is_valid_record_id(record_id):
        raise ApiError(400, "Invalid ID")


def load_record(record_id: str) -> dict:
    """The record for a path parameter, or a 400 / 404."""
    require_valid_id(record_id)
    record = get_record(record_id)
    if not record:
        raise ApiError(404, "Not found")
    return record


def load_record_for_retranscribe(record_id: str) -> dict:
    record = load_record(record_id)
    if record["status"] == STATUS_IN_PROGRESS:
        raise ApiError(409, "Record is currently being processed")
    return record


async def _as_sse(events: AsyncIterator[Event]) -> AsyncIterator[dict]:
    """Serialize use-case events into SSE messages."""
    async for name, payload in events:
        yield {"event": name, "data": json.dumps(payload)}


def transcription_stream(
    request: Request,
    url: str,
    options: TranscriptionOptions,
    record: dict | None = None,
    steps: Steps | None = None,
) -> EventSourceResponse:
    """Run a transcription and stream its progress; the client disconnecting cancels it."""
    return EventSourceResponse(_as_sse(run_transcription(
        url, options.model,
        duration_limit=options.duration_limit_seconds,
        is_cancelled=request.is_disconnected,
        record=record,
        steps=steps,
    )))


async def summarize(
    record_id: str,
    req: SummarizeRequest,
    summarizer: Callable[..., Awaitable[str]] | None = None,
) -> dict:
    record = load_record(record_id)
    try:
        return await summarize_record(record, req.prompt, model=req.model, summarize=summarizer)
    except RecordNotCompleted:
        raise ApiError(400, "Record is not completed") from None
    except SummaryNotSaved:
        raise ApiError(500, "Failed to save summary") from None
    except Exception as e:
        logger.error("Summarize error for %s: %s", record_id, e, exc_info=True)
        raise ApiError(500, f"Summarization failed: {e}") from e
