import pytest

from app.usecases.errors import ModelNotAvailable, RecordNotCompleted, RecordNotFound, SummaryNotSaved

URL = "https://www.youtube.com/watch?v=abc123xyz"


@pytest.fixture
def done_id(env) -> str:
    rid = env.history.records.create("Vid", URL, 60)
    env.history.records.complete(rid, "Vid\n\ntranscript text")
    return rid


class TestSummarizeRecord:
    @pytest.mark.asyncio
    async def test_saves_summary_under_a_title_heading(self, env, done_id):
        result = await env.summarization().summarize_record(done_id, "  Custom  ", "m")
        assert result == {"summary": "# Vid\n\nThe summary", "prompt": "Custom", "model": "m"}
        env.summarize.assert_awaited_once_with("Vid\n\ntranscript text", "Custom", "m")
        saved = env.history.summaries.get(done_id)
        assert saved["summary"] == "# Vid\n\nThe summary"
        assert saved["prompt"] == "Custom"

    @pytest.mark.asyncio
    async def test_model_is_resolved_once_and_stored(self, env, done_id):
        """The default is applied here, so what ran is what the sidecar says ran."""
        result = await env.summarization().summarize_record(done_id, "")
        assert result["model"] == "gpt-6-luna"
        env.check_model.assert_called_once_with("gpt-6-luna")
        assert env.summarize.await_args.args[2] == "gpt-6-luna"
        assert env.history.summaries.get(done_id)["model"] == "gpt-6-luna"

    @pytest.mark.asyncio
    async def test_only_a_completed_record_can_be_summarized(self, env):
        rid = env.history.records.create("Vid", URL, 60)  # still in progress
        with pytest.raises(RecordNotCompleted):
            await env.summarization().summarize_record(rid, "")
        env.summarize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_record(self, env):
        with pytest.raises(RecordNotFound):
            await env.summarization().summarize_record("00000000", "")

    @pytest.mark.asyncio
    async def test_unavailable_model_is_refused_before_the_api_is_called(self, env, done_id):
        env.check_model.side_effect = ModelNotAvailable("no key")
        with pytest.raises(ModelNotAvailable):
            await env.summarization().summarize_record(done_id, "")
        env.summarize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_save_is_reported(self, env, done_id, monkeypatch):
        monkeypatch.setattr(env.history.summaries, "save", lambda *args: False)
        with pytest.raises(SummaryNotSaved):
            await env.summarization().summarize_record(done_id, "")
