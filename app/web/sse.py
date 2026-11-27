"""Use-case events → a Server-Sent Events response."""

import json
from collections.abc import AsyncIterator

from sse_starlette.sse import EventSourceResponse

from app.usecases.transcription import Event


class EventStream:
    def __init__(self, events: AsyncIterator[Event]) -> None:
        self._events = events

    def response(self) -> EventSourceResponse:
        return EventSourceResponse(self._messages())

    async def _messages(self) -> AsyncIterator[dict]:
        async for name, payload in self._events:
            yield {"event": name, "data": json.dumps(payload)}
