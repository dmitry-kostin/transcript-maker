"""Demo routes (`/api/demo/...`): the same handlers as the real ones, built with the simulated use cases."""
from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from app.web.routes import WorkRoutes
from app.web.schemas import DemoTranscribeRequest


class DemoWorkRoutes(WorkRoutes):
    prefix = "/api/demo"

    async def transcribe(self, req: DemoTranscribeRequest, request: Request) -> EventSourceResponse:
        return self._transcribe_url(req.url, req, request)
