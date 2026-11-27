"""The transcription use case, driven without HTTP: the outside world is passed in as fake Steps."""

import pytest

from app.usecases.errors import ModelNotAvailable, RecordBusy, RecordFromOtherMode, RecordNotFound
from app.usecases.steps import DownloadFailed
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

        record = env.history.records.get(payload["record_id"])
        assert record["status"] == "done"
        assert record["body"] == payload["text"]
        assert record["model"] == "gpt-transcribe"

    @pytest.mark.asyncio
    async def test_duration_limit_is_stored_and_passed_to_chunker(self, env):
        events = await env.run(duration_limit=300)
        assert env.chunk_limit_seen == 300
        assert env.history.records.get(events[-2][1]["record_id"])["duration_limit"] == 300

    @pytest.mark.asyncio
    async def test_too_long_video_is_rejected_before_a_record_exists(self, env):
        env.download.return_value = (env.audio, 5 * 60 * 60, "Long")
        events = await env.run()
        assert events[-1][0] == "error"
        assert "Max is 4 hours" in events[-1][1]["message"]
        assert env.history.records.list_all() == []

    @pytest.mark.asyncio
    async def test_too_long_video_is_refused_before_it_is_downloaded(self, env):
        """The download step reports the length first; the use case says no and nothing is fetched."""
        fetched = False

        async def download(url, accept):
            nonlocal fetched
            accept(5 * 60 * 60)
            fetched = True
            return env.audio, 5 * 60 * 60, "Long"

        env.download.side_effect = download
        events = await env.run()
        assert events[-1][0] == "error"
        assert "Max is 4 hours" in events[-1][1]["message"]
        assert not fetched
        assert env.history.records.list_all() == []

    @pytest.mark.asyncio
    async def test_unavailable_model_is_refused_before_anything_starts(self, env):
        env.check_model.side_effect = ModelNotAvailable("TM_OPENAI_API_KEY env var is required")
        with pytest.raises(ModelNotAvailable):
            await env.run()
        env.check_model.assert_called_once_with("gpt-transcribe")
        env.download.assert_not_awaited()
        assert env.history.records.list_all() == []

    @pytest.mark.asyncio
    async def test_first_minutes_of_a_too_long_video_are_accepted(self, env):
        env.download.return_value = (env.audio, 5 * 60 * 60, "Long")
        events = await env.run(duration_limit=600)
        assert events[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_download_error_is_reported_without_a_record(self, env):
        env.download.side_effect = DownloadFailed("Video unavailable")
        events = await env.run()
        assert events[-1] == ("error", {"message": "Video unavailable", "record_id": None})
        assert env.history.records.list_all() == []

    @pytest.mark.asyncio
    async def test_empty_chunk_fails_the_record(self, env):
        env.transcribe.side_effect = None
        env.transcribe.return_value = "  "
        events = await env.run()
        name, payload = events[-1]
        assert name == "error"
        record = env.history.records.get(payload["record_id"])
        assert record["status"] == "error"
        assert "empty transcription" in record["error"]

    @pytest.mark.asyncio
    async def test_cancel_after_download_creates_nothing(self, env):
        async def cancelled() -> bool:
            return True

        events = await env.run(is_cancelled=cancelled)
        assert [name for name, _ in events] == ["progress", "progress"]
        assert env.history.records.list_all() == []
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
        record = env.history.records.list_all()[0]
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
        assert len(env.history.records.list_all()) == 2

    @pytest.mark.asyncio
    async def test_temp_audio_is_cleaned_up(self, env):
        await env.run()
        assert not env.audio.exists()

    @pytest.mark.asyncio
    async def test_run_is_registered_as_active_only_while_it_runs(self, env):
        """Cleanup asks this registry what is alive."""
        seen: list[tuple[bool, set[str]]] = []

        async def download(url, accept):
            seen.append((env.active_runs.any_active(), env.active_runs.record_ids()))
            return env.audio, 120, "My Video"

        async def transcribe(path, model):
            seen.append((env.active_runs.any_active(), env.active_runs.record_ids()))
            return "text"

        env.download.side_effect = download
        env.transcribe.side_effect = transcribe
        events = await env.run()
        record_id = events[-2][1]["record_id"]
        assert seen == [(True, set()), (True, {record_id})]
        assert not env.active_runs.any_active()

    @pytest.mark.asyncio
    async def test_failed_run_is_no_longer_active(self, env):
        env.transcribe.side_effect = RuntimeError("boom")
        await env.run()
        assert not env.active_runs.any_active()


class TestRetranscription:
    def _existing(self, env) -> dict:
        rid = env.history.records.create("Old Title", URL, 90, model="gpt-4o-transcribe-diarize")
        env.history.records.complete(rid, "Old Title\n\nold text")
        env.history.audio.save(rid, env.audio)
        return env.history.records.get(rid)

    @pytest.mark.asyncio
    async def test_missing_record_is_refused(self, env):
        with pytest.raises(RecordNotFound):
            await env.run(record_id="00000000")

    @pytest.mark.asyncio
    async def test_record_in_progress_is_refused(self, env):
        rid = env.history.records.create("Running", URL, 90)
        with pytest.raises(RecordBusy):
            await env.run(record_id=rid)
        env.download.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_demo_record_is_refused_by_real_steps(self, env):
        rid = env.history.records.create("Demo: x", "x", 90, demo=True)
        env.history.records.complete(rid, "Demo: x\n\nplaceholder")
        with pytest.raises(RecordFromOtherMode):
            await env.run(record_id=rid)
        assert env.history.records.get(rid)["status"] == "done"

    @pytest.mark.asyncio
    async def test_rewrites_the_same_record_from_cached_audio(self, env):
        record = self._existing(env)
        events = await env.run(record_id=record["id"], duration_limit=60)
        env.download.assert_not_awaited()
        payload = events[-2][1]
        assert payload["record_id"] == record["id"]
        assert payload["title"] == "Old Title"
        assert payload["duration_seconds"] == 90

        updated = env.history.records.get(record["id"])
        assert updated["status"] == "done"
        assert updated["model"] == "gpt-transcribe"
        assert updated["duration_limit"] == 60
        assert updated["body"].startswith("Old Title\n\ntext of retranscribe_")
        assert len(env.history.records.list_all()) == 1

    @pytest.mark.asyncio
    async def test_long_recording_is_not_blocked_on_retranscribe(self, env):
        rid = env.history.records.create("Long", URL, 5 * 60 * 60)
        env.history.records.complete(rid, "Long\n\nold text")
        env.history.audio.save(rid, env.audio)
        events = await env.run(record_id=rid)
        assert events[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_resumes_from_chunk_cache(self, env):
        record = self._existing(env)
        env.chunks = [env.audio, env.add_chunk("x_chunk1.mp3")]
        env.history.chunks.save(record["id"], "gpt-transcribe", 2, ["first part"])

        events = await env.run(record_id=record["id"])
        assert env.transcribe.await_count == 1
        assert events[-2][1]["text"] == "Old Title\n\nfirst part text of x_chunk1.mp3"

    @pytest.mark.asyncio
    async def test_chunk_cache_from_another_duration_limit_is_not_reused(self, env):
        record = self._existing(env)
        env.chunks = [env.audio, env.add_chunk("x_chunk1.mp3")]
        env.history.chunks.save(record["id"], "gpt-transcribe", 2, ["first part", "second part"])  # made with no limit

        await env.run(record_id=record["id"], duration_limit=900)
        assert env.transcribe.await_count == 2

    @pytest.mark.asyncio
    async def test_failure_marks_record_failed_but_keeps_the_old_transcript(self, env):
        record = self._existing(env)
        env.transcribe.side_effect = RuntimeError("boom")
        events = await env.run(record_id=record["id"])
        assert events[-1] == ("error", {"message": "An error occurred: boom", "record_id": record["id"]})
        failed = env.history.records.get(record["id"])
        assert failed["status"] == "error"
        assert failed["body"] == "Old Title\n\nold text"

    @pytest.mark.asyncio
    async def test_download_failure_leaves_the_record_untouched(self, env):
        """Nothing was changed yet, so a finished record stays finished."""
        rid = env.history.records.create("Old Title", URL, 90, model="m1")
        env.history.records.complete(rid, "Old Title\n\nold text")  # no cached audio → must download
        env.download.side_effect = DownloadFailed("Video unavailable")
        events = await env.run(record_id=rid)
        assert events[-1] == ("error", {"message": "Video unavailable", "record_id": rid})
        untouched = env.history.records.get(rid)
        assert untouched["status"] == "done"
        assert untouched["model"] == "m1"
        assert untouched["body"] == "Old Title\n\nold text"

    @pytest.mark.asyncio
    async def test_record_taken_by_another_run_is_not_touched(self, env):
        """A second re-transcription that loses the race reports it and leaves the first one alone."""
        rid = env.history.records.create("Old Title", URL, 90, model="m1")
        env.history.records.complete(rid, "Old Title\n\nold text")  # no cached audio → must download

        async def download_while_the_other_run_takes_it(url, accept):
            env.history.records.reset(rid, model="other-run")
            return env.audio, 90, "Old Title"

        env.download.side_effect = download_while_the_other_run_takes_it
        events = await env.run(record_id=rid)
        assert events[-1][0] == "error"
        assert "another request" in events[-1][1]["message"]
        env.transcribe.assert_not_awaited()
        still_running = env.history.records.get(rid)
        assert still_running["status"] == "in_progress"
        assert still_running["model"] == "other-run"


class TestSaving:
    @pytest.mark.asyncio
    async def test_a_transcript_that_cannot_be_saved_is_an_error_not_a_result(self, env):
        """The record vanished under the run (its file was removed): never report a transcript that is not kept."""

        def record_vanishes(path, model):
            for record in env.history.records.list_all():
                env.history.records.delete(record["id"])
            return "text"

        env.transcribe.side_effect = record_vanishes
        events = await env.run()
        assert [name for name, _ in events if name in ("transcript", "done")] == []
        assert events[-1][0] == "error"
        assert "could not be saved" in events[-1][1]["message"]
