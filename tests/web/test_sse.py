import json

import pytest

from app.web.sse import EventStream


class TestEventStream:
    @pytest.mark.asyncio
    async def test_events_become_sse_messages(self):
        class Run:
            async def events(self):
                yield "progress", {"stage": "downloading", "message": "…"}
                yield "done", {}

        messages = [message async for message in EventStream(Run().events())._messages()]
        assert [message["event"] for message in messages] == ["progress", "done"]
        assert json.loads(messages[0]["data"]) == {"stage": "downloading", "message": "…"}
        assert messages[1]["data"] == "{}"
