from unittest.mock import AsyncMock

import pytest

import app.usecases.summarization as summarization
from app.storage.history import complete_record, create_record, get_record, get_summary
from app.usecases.summarization import RecordNotCompleted, SummaryNotSaved, summarize_record

URL = "https://www.youtube.com/watch?v=abc123xyz"


@pytest.fixture
def done_record(tmp_results) -> dict:
    rid = create_record("Vid", URL, 60)
    complete_record(rid, "Vid\n\ntranscript text")
    return get_record(rid)


class TestSummarizeRecord:
    @pytest.mark.asyncio
    async def test_saves_summary_under_a_title_heading(self, done_record):
        summarize = AsyncMock(return_value="The summary")
        result = await summarize_record(done_record, "  Custom  ", model="m", summarize=summarize)
        assert result == {"summary": "# Vid\n\nThe summary", "prompt": "Custom"}
        summarize.assert_awaited_once_with("Vid\n\ntranscript text", "  Custom  ", model="m")
        saved = get_summary(done_record["id"])
        assert saved["summary"] == "# Vid\n\nThe summary"
        assert saved["prompt"] == "Custom"

    @pytest.mark.asyncio
    async def test_only_a_completed_record_can_be_summarized(self, tmp_results):
        rid = create_record("Vid", URL, 60)  # still in progress
        summarize = AsyncMock()
        with pytest.raises(RecordNotCompleted):
            await summarize_record(get_record(rid), "", summarize=summarize)
        summarize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_save_is_reported(self, done_record, monkeypatch):
        monkeypatch.setattr(summarization, "save_summary", lambda *args: False)
        with pytest.raises(SummaryNotSaved):
            await summarize_record(done_record, "", summarize=AsyncMock(return_value="x"))
