"""The transcription use case, driven without HTTP: the outside world is passed in as fake Steps."""
import pytest

from app.services.downloader import DownloadError
from app.storage.history import (
    complete_record, create_record, get_history, get_record, reset_record, save_audio, save_chunk_cache,
)
from tests.usecases.conftest import URL


class TestNewTranscription:
    @pytest.mark.asyncio
    async def test_happy_path_saves_record_with_title(self, env):
        events = await env.run()
        names = [name for name, _ in events]
        assert names[-2:] == ["transcript", "done"]
        payload = events[-2][1]
        assert payload["text"] == "My Video\n\ntext of video_11111111.mp3"
        assert payload["model"] == "gpt-transcribe"

        record = get_record(payload["record_id"])
        assert record["status"] == "done"
        assert record["body"] == payload["text"]
        assert record["model"] == "gpt-transcribe"

    @pytest.mark.asyncio
    async def test_duration_limit_is_stored_and_passed_to_chunker(self, env):
        events = await env.run(duration_limit=300)
        assert env.chunk_limit_seen == 300
        assert get_record(events[-2][1]["record_id"])["duration_limit"] == 300

    @pytest.mark.asyncio
    async def test_too_long_video_is_rejected_before_a_record_exists(self, env):
        env.download.return_value = (env.audio, 5 * 60 * 60, "Long")
        events = await env.run()
        assert events[-1][0] == "error"
        assert "Max is 4 hours" in events[-1][1]["message"]
        assert get_history() == []

    @pytest.mark.asyncio
    async def test_first_minutes_of_a_too_long_video_are_accepted(self, env):
        env.download.return_value = (env.audio, 5 * 60 * 60, "Long")
        events = await env.run(duration_limit=600)
        assert events[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_download_error_is_reported_without_a_record(self, env):
        env.download.side_effect = DownloadError("Video unavailable")
        events = await env.run()
        assert events[-1] == ("error", {"message": "Video unavailable", "record_id": None})
        assert get_history() == []

    @pytest.mark.asyncio
    async def test_empty_chunk_fails_the_record(self, env):
        env.transcribe.side_effect = None
        env.transcribe.return_value = "  "
        events = await env.run()
        name, payload = events[-1]
        assert name == "error"
        record = get_record(payload["record_id"])
        assert record["status"] == "error"
        assert "empty transcription" in record["error"]

    @pytest.mark.asyncio
    async def test_cancel_after_download_creates_nothing(self, env):
        async def cancelled() -> bool:
            return True
        events = await env.run(is_cancelled=cancelled)
        assert [name for name, _ in events] == ["progress", "progress"]
        assert get_history() == []
        env.transcribe.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_mid_transcription_marks_record_failed(self, env):
        """The client goes away while the first chunk is being transcribed."""
        gone = False

        async def transcribe_then_disconnect(path, model=""):
            nonlocal gone
            gone = True
            return "first chunk"

        async def is_cancelled() -> bool:
            return gone

        env.transcribe.side_effect = transcribe_then_disconnect
        env.chunks = [env.audio, env.add_chunk("video_11111111_chunk1.mp3")]
        events = await env.run(is_cancelled=is_cancelled)
        assert "transcript" not in [name for name, _ in events]
        assert env.transcribe.await_count == 1
        record = get_history()[0]
        assert record["status"] == "error"
        assert record["error"] == "Transcription interrupted"

    @pytest.mark.asyncio
    async def test_multi_chunk_reports_progress_and_joins_text(self, env):
        env.chunks = [env.audio, env.add_chunk("video_11111111_chunk1.mp3")]
        events = await env.run()
        transcribing = [p for name, p in events if name == "progress" and p.get("chunk")]
        assert [(p["chunk"], p["chunks_total"]) for p in transcribing] == [(1, 2), (2, 2)]
        assert "eta_seconds" in transcribing[1]
        assert events[-2][1]["text"] == "My Video\n\ntext of video_11111111.mp3 text of video_11111111_chunk1.mp3"

    @pytest.mark.asyncio
    async def test_same_url_reuses_cached_audio(self, env):
        await env.run()
        env.download.reset_mock()
        events = await env.run()
        env.download.assert_not_awaited()
        assert events[0][1]["message"] == "Using cached audio..."
        assert events[-2][1]["title"] == "My Video"
        assert len(get_history()) == 2

    @pytest.mark.asyncio
    async def test_temp_audio_is_cleaned_up(self, env):
        await env.run()
        assert not env.audio.exists()


class TestRetranscription:
    def _existing(self, env) -> dict:
        rid = create_record("Old Title", URL, 90, model="gpt-4o-transcribe-diarize")
        complete_record(rid, "Old Title\n\nold text")
        save_audio(rid, env.audio)
        return get_record(rid)

    @pytest.mark.asyncio
    async def test_rewrites_the_same_record_from_cached_audio(self, env):
        record = self._existing(env)
        events = await env.run(record=record, duration_limit=60)
        env.download.assert_not_awaited()
        payload = events[-2][1]
        assert payload["record_id"] == record["id"]
        assert payload["title"] == "Old Title"
        assert payload["duration_seconds"] == 90

        updated = get_record(record["id"])
        assert updated["status"] == "done"
        assert updated["model"] == "gpt-transcribe"
        assert updated["duration_limit"] == 60
        assert updated["body"].startswith("Old Title\n\ntext of retranscribe_")
        assert len(get_history()) == 1

    @pytest.mark.asyncio
    async def test_long_recording_is_not_blocked_on_retranscribe(self, env):
        rid = create_record("Long", URL, 5 * 60 * 60)
        complete_record(rid, "Long\n\nold text")
        save_audio(rid, env.audio)
        events = await env.run(record=get_record(rid))
        assert events[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_resumes_from_chunk_cache(self, env):
        record = self._existing(env)
        env.chunks = [env.audio, env.add_chunk("x_chunk1.mp3")]
        save_chunk_cache(record["id"], "gpt-transcribe", 2, ["first part"])

        events = await env.run(record=record)
        assert env.transcribe.await_count == 1
        assert events[-2][1]["text"] == "Old Title\n\nfirst part text of x_chunk1.mp3"

    @pytest.mark.asyncio
    async def test_failure_marks_record_failed_but_keeps_the_old_transcript(self, env):
        record = self._existing(env)
        env.transcribe.side_effect = RuntimeError("boom")
        events = await env.run(record=record)
        assert events[-1] == ("error", {"message": "An error occurred: boom", "record_id": record["id"]})
        failed = get_record(record["id"])
        assert failed["status"] == "error"
        assert failed["body"] == "Old Title\n\nold text"

    @pytest.mark.asyncio
    async def test_download_failure_leaves_the_record_untouched(self, env):
        """Nothing was changed yet, so a finished record stays finished."""
        rid = create_record("Old Title", URL, 90, model="m1")
        complete_record(rid, "Old Title\n\nold text")  # no cached audio → must download
        env.download.side_effect = DownloadError("Video unavailable")
        events = await env.run(record=get_record(rid))
        assert events[-1] == ("error", {"message": "Video unavailable", "record_id": rid})
        untouched = get_record(rid)
        assert untouched["status"] == "done"
        assert untouched["model"] == "m1"
        assert untouched["body"] == "Old Title\n\nold text"

    @pytest.mark.asyncio
    async def test_record_taken_by_another_run_is_not_touched(self, env):
        """A second re-transcription that loses the race reports it and leaves the first one alone."""
        record = self._existing(env)
        reset_record(record["id"], model="other-run")  # the first run got there between lookup and open

        events = await env.run(record=record)
        assert events[-1][0] == "error"
        assert "another request" in events[-1][1]["message"]
        env.transcribe.assert_not_awaited()
        still_running = get_record(record["id"])
        assert still_running["status"] == "in_progress"
        assert still_running["model"] == "other-run"
