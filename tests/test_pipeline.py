"""The transcription use case, driven without HTTP: every external dependency is mocked."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import app.pipeline as pipeline
from app.downloader import DownloadError
from app.history import complete_record, create_record, get_history, get_record, save_audio, save_chunk_cache
from app.pipeline import run_transcription

URL = "https://www.youtube.com/watch?v=abc123xyz"


async def _never_cancelled() -> bool:
    return False


async def _collect(**kwargs) -> list[tuple[str, dict]]:
    kwargs.setdefault("requested_model", "")
    kwargs.setdefault("duration_limit", 0)
    kwargs.setdefault("is_cancelled", _never_cancelled)
    return [event async for event in run_transcription(URL, **kwargs)]


@pytest.fixture
def env(tmp_results, tmp_path, monkeypatch):
    """Temp dirs plus a fake download, chunker and transcriber."""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(pipeline.settings, "temp_dir", str(tmp_dir))
    monkeypatch.setattr(pipeline.settings, "transcribe_model", "gpt-transcribe")

    audio = tmp_dir / "video_11111111.mp3"
    audio.write_bytes(b"\x00" * 2048)

    class Env:
        download = AsyncMock(return_value=(audio, 120, "My Video"))
        chunks: list | None = None  # None → the audio the pipeline prepared, unsplit
        transcribe = AsyncMock(side_effect=lambda path, model="": f"text of {Path(path).name}")

    Env.audio = audio
    with patch("app.pipeline.download_audio", Env.download), \
         patch("app.pipeline.prepare_chunks", lambda path, duration_limit=None: Env.chunks or [path]), \
         patch("app.pipeline.transcribe_chunk", Env.transcribe):
        yield Env


class TestNewTranscription:
    @pytest.mark.asyncio
    async def test_happy_path_saves_record_with_title(self, env):
        events = await _collect()
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
        seen = {}

        def chunker(path, duration_limit=None):
            seen["limit"] = duration_limit
            return [path]

        with patch("app.pipeline.prepare_chunks", chunker):
            events = await _collect(duration_limit=300)
        assert seen["limit"] == 300
        assert get_record(events[-2][1]["record_id"])["duration_limit"] == 300

    @pytest.mark.asyncio
    async def test_too_long_video_is_rejected_before_a_record_exists(self, env):
        env.download.return_value = (env.audio, 5 * 60 * 60, "Long")
        events = await _collect()
        assert events[-1][0] == "error"
        assert "Max is 4 hours" in events[-1][1]["message"]
        assert get_history() == []

    @pytest.mark.asyncio
    async def test_download_error_is_reported_without_a_record(self, env):
        env.download.side_effect = DownloadError("Video unavailable")
        events = await _collect()
        assert events[-1] == ("error", {"message": "Video unavailable", "record_id": None})
        assert get_history() == []

    @pytest.mark.asyncio
    async def test_empty_chunk_fails_the_record(self, env):
        env.transcribe.side_effect = None
        env.transcribe.return_value = "  "
        events = await _collect()
        name, payload = events[-1]
        assert name == "error"
        record = get_record(payload["record_id"])
        assert record["status"] == "error"
        assert "empty transcription" in record["error"]

    @pytest.mark.asyncio
    async def test_cancel_after_download_creates_nothing(self, env):
        async def cancelled() -> bool:
            return True
        events = await _collect(is_cancelled=cancelled)
        assert [name for name, _ in events] == ["progress", "progress"]
        assert get_history() == []
        env.transcribe.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_mid_transcription_marks_record_failed(self, env):
        calls = {"n": 0}

        async def cancel_on_third_check() -> bool:
            calls["n"] += 1
            return calls["n"] >= 3

        chunk_b = env.audio.with_name("video_11111111_chunk1.mp3")
        chunk_b.write_bytes(b"\x00" * 1024)
        env.chunks = [env.audio, chunk_b]
        events = await _collect(is_cancelled=cancel_on_third_check)
        assert "transcript" not in [name for name, _ in events]
        record = get_history()[0]
        assert record["status"] == "error"
        assert record["error"] == "Transcription interrupted"

    @pytest.mark.asyncio
    async def test_multi_chunk_reports_progress_and_joins_text(self, env):
        chunk_b = env.audio.with_name("video_11111111_chunk1.mp3")
        chunk_b.write_bytes(b"\x00" * 1024)
        env.chunks = [env.audio, chunk_b]
        events = await _collect()
        transcribing = [p for name, p in events if name == "progress" and p.get("chunk")]
        assert [(p["chunk"], p["chunks_total"]) for p in transcribing] == [(1, 2), (2, 2)]
        assert "eta_seconds" in transcribing[1]
        assert events[-2][1]["text"] == "My Video\n\ntext of video_11111111.mp3 text of video_11111111_chunk1.mp3"

    @pytest.mark.asyncio
    async def test_same_url_reuses_cached_audio(self, env):
        await _collect()
        env.download.reset_mock()
        events = await _collect()
        env.download.assert_not_awaited()
        assert events[0][1]["message"] == "Using cached audio..."
        assert events[-2][1]["title"] == "My Video"
        assert len(get_history()) == 2

    @pytest.mark.asyncio
    async def test_temp_audio_is_cleaned_up(self, env):
        await _collect()
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
        events = await _collect(record=record, duration_limit=60)
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
        save_audio(rid, env.audio)
        events = await _collect(record=get_record(rid))
        assert events[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_resumes_from_chunk_cache(self, env):
        record = self._existing(env)
        chunk_b = env.audio.with_name("x_chunk1.mp3")
        chunk_b.write_bytes(b"\x00" * 1024)
        env.chunks = [env.audio, chunk_b]
        save_chunk_cache(record["id"], "gpt-transcribe", 2, ["first part"])

        events = await _collect(record=record)
        assert env.transcribe.await_count == 1
        assert events[-2][1]["text"] == "Old Title\n\nfirst part text of x_chunk1.mp3"

    @pytest.mark.asyncio
    async def test_failure_marks_existing_record_failed(self, env):
        record = self._existing(env)
        env.transcribe.side_effect = RuntimeError("boom")
        events = await _collect(record=record)
        assert events[-1] == ("error", {"message": "An error occurred: boom", "record_id": record["id"]})
        assert get_record(record["id"])["status"] == "error"
