"""HTTP routes. Validation, status codes and SSE only — the work happens in ``app.usecases``."""

import logging

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.storage.results_directory import RecordId
from app.usecases.errors import UseCaseError
from app.usecases.maintenance import Maintenance
from app.usecases.providers import Providers
from app.usecases.records import Records
from app.usecases.summarization import Summarization
from app.usecases.transcription import Transcription
from app.web.errors import ApiError
from app.web.schemas import RetranscribeRequest, SummarizeRequest, TranscribeRequest, TranscriptionOptions
from app.web.sse import EventStream

logger = logging.getLogger(__name__)


class Routes:
    """What every group of routes shares."""

    def _require_valid_id(self, record_id: str) -> None:
        """Reject a malformed path parameter before it reaches a use case."""
        if not RecordId.is_valid(record_id):
            raise ApiError(400, "Invalid ID")


class ReadRoutes(Routes):
    """Looking at records, providers and housekeeping — the same in real and demo mode."""

    def __init__(self, records: Records, providers: Providers, maintenance: Maintenance) -> None:
        self._records = records
        self._providers = providers
        self._maintenance = maintenance

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/api")
        router.add_api_route("/providers", self.get_providers, methods=["GET"])
        router.add_api_route("/history", self.history, methods=["GET"])
        router.add_api_route("/history/{record_id}", self.get_record, methods=["GET"])
        router.add_api_route("/history/{record_id}", self.delete_record, methods=["DELETE"])
        router.add_api_route("/history/{record_id}/reveal", self.reveal_in_finder, methods=["POST"])
        router.add_api_route("/history/{record_id}/summary", self.get_summary, methods=["GET"])
        router.add_api_route("/cleanup", self.cleanup, methods=["POST"])
        return router

    async def get_providers(self) -> dict:
        """Available model providers, based on configured API keys."""
        return {"providers": self._providers.available()}

    async def history(self) -> list[dict]:
        return self._records.list_all()

    async def get_record(self, record_id: str) -> dict:
        self._require_valid_id(record_id)
        return self._records.load(record_id)

    async def delete_record(self, record_id: str) -> dict:
        self._require_valid_id(record_id)
        self._records.remove(record_id)
        return {"ok": True}

    async def reveal_in_finder(self, record_id: str) -> dict:
        self._require_valid_id(record_id)
        self._records.reveal(record_id)
        return {"ok": True}

    async def get_summary(self, record_id: str) -> dict:
        self._require_valid_id(record_id)
        return self._records.load_summary(record_id)

    async def cleanup(self) -> dict:
        """Fail in_progress records no run is working on and delete leftover temp files."""
        return self._maintenance.clean_up()


class WorkRoutes(Routes):
    """The endpoints that do work: transcribe, retranscribe, summarize.

    Real and demo mode are this same class built with different use cases, so both
    validate, guard and fail identically. A subclass names its URL prefix and the
    request body its ``transcribe`` endpoint accepts. A client that disconnects
    cancels its transcription.
    """

    prefix: str

    def __init__(self, transcription: Transcription, summarization: Summarization) -> None:
        self._transcription = transcription
        self._summarization = summarization

    def router(self) -> APIRouter:
        router = APIRouter(prefix=self.prefix)
        router.add_api_route("/transcribe", self.transcribe, methods=["POST"])
        router.add_api_route("/history/{record_id}/retranscribe", self.retranscribe, methods=["POST"])
        router.add_api_route("/history/{record_id}/summarize", self.summarize, methods=["POST"])
        return router

    async def transcribe(self, req: TranscriptionOptions, request: Request) -> EventSourceResponse:
        raise NotImplementedError

    async def retranscribe(self, record_id: str, req: RetranscribeRequest, request: Request) -> EventSourceResponse:
        self._require_valid_id(record_id)
        events = self._transcription.retranscribe(
            record_id,
            req.model,
            req.duration_limit_seconds,
            request.is_disconnected,
        )
        return EventStream(events).response()

    async def summarize(self, record_id: str, req: SummarizeRequest) -> dict:
        self._require_valid_id(record_id)
        try:
            return await self._summarization.summarize_record(record_id, req.prompt, req.model)
        except UseCaseError:
            raise
        except Exception as e:
            logger.error("Summarize error for %s: %s", record_id, e, exc_info=True)
            raise ApiError(500, f"Summarization failed: {e}") from e

    def _transcribe_url(self, url: str, req: TranscriptionOptions, request: Request) -> EventSourceResponse:
        events = self._transcription.transcribe_url(
            url,
            req.model,
            req.duration_limit_seconds,
            request.is_disconnected,
        )
        return EventStream(events).response()


class RealWorkRoutes(WorkRoutes):
    prefix = "/api"

    async def transcribe(self, req: TranscribeRequest, request: Request) -> EventSourceResponse:
        return self._transcribe_url(req.url, req, request)
