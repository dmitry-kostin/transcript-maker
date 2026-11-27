
from app.storage.record_store import SLUG_MAX_LENGTH


class TestSlug:
    """The title's part of the record filename."""

    def _slug(self, history, title: str) -> str:
        rid = history.records.create(title, "https://youtube.com/watch?v=a", 60)
        return history.records.file_path(rid).name.removesuffix(f"_{rid}.md")

    def test_basic(self, history):
        assert self._slug(history, "Hello World") == "hello-world"

    def test_special_chars(self, history):
        assert self._slug(history, "Rick Astley - Never Gonna Give You Up!") == "rick-astley---never-gonna-give-you-up"

    def test_empty_and_only_special_chars(self, history):
        assert self._slug(history, "") == "untitled"
        assert self._slug(history, "!!!???") == "untitled"

    def test_truncation(self, history):
        assert len(self._slug(history, "a" * 100)) == SLUG_MAX_LENGTH

    def test_unicode(self, history):
        assert self._slug(history, "Привет мир")  # Should produce something, not empty


class TestLifecycle:
    def test_create_and_list(self, tmp_results, history):
        rid = history.records.create("My Video", "https://youtube.com/watch?v=abc", 120)
        assert len(rid) == 8
        records = history.records.list_all()
        assert len(records) == 1
        assert records[0]["status"] == "in_progress"
        assert records[0]["title"] == "My Video"

    def test_complete(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "This is the transcript text.")
        records = history.records.list_all()
        assert records[0]["status"] == "done"

    def test_fail(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.fail(rid, "API timeout")
        records = history.records.list_all()
        assert records[0]["status"] == "error"
        assert records[0]["error"] == "API timeout"

    def test_delete(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        assert history.records.delete(rid) is True
        assert history.records.list_all() == []

    def test_delete_nonexistent(self, tmp_results, history):
        assert history.records.delete("00000000") is False

    def test_history_sorted_newest_first(self, tmp_results, history):
        import time
        rid1 = history.records.create("First", "https://youtube.com/watch?v=a", 10)
        time.sleep(1.1)  # created_at has second-level precision
        rid2 = history.records.create("Second", "https://youtube.com/watch?v=b", 20)
        records = history.records.list_all()
        assert records[0]["title"] == "Second"
        assert records[1]["title"] == "First"

    def test_get_result_path(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        path = history.records.file_path(rid)
        assert path is not None
        assert path.exists()

    def test_resolve_rejects_bad_ids(self, tmp_results, history):
        assert history.records.file_path("not-hex!") is None
        assert history.records.file_path("") is None
        assert history.records.file_path("abcd12345") is None  # 9 chars
        assert history.records.file_path("abcd123") is None  # 7 chars

    def test_complete_nonexistent_is_safe(self, tmp_results, history):
        # Should not raise
        history.records.complete("00000000", "text")
        history.records.fail("00000000", "error")


class TestEdgeCases:
    def test_title_with_colons(self, tmp_results, history):
        rid = history.records.create("Live: Breaking News: Update", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Some transcript")
        records = history.records.list_all()
        assert records[0]["title"] == "Live: Breaking News: Update"

    def test_title_with_yaml_special_chars(self, tmp_results, history):
        rid = history.records.create("[Official] #1 Hit {2026}", "https://youtube.com/watch?v=abc", 60)
        records = history.records.list_all()
        assert records[0]["title"] == "[Official] #1 Hit {2026}"

    def test_very_long_title(self, tmp_results, history):
        long_title = "A" * 500
        rid = history.records.create(long_title, "https://youtube.com/watch?v=abc", 60)
        records = history.records.list_all()
        assert records[0]["title"] == long_title
        # Slug is truncated but file exists
        path = history.records.file_path(rid)
        assert path is not None

    def test_corrupt_md_skipped_in_history(self, tmp_results, history):
        # Create a valid record
        rid = history.records.create("Good", "https://youtube.com/watch?v=abc", 60)
        # Write a file with no valid ID in the filename — should be skipped
        corrupt = tmp_results / "corrupt-no-id.md"
        corrupt.write_text("this is not valid frontmatter at all")
        records = history.records.list_all()
        # Should only see the valid record, corrupt one skipped
        assert len(records) == 1
        assert records[0]["title"] == "Good"


class TestGetRecord:
    def test_existing(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Some text")
        record = history.records.get(rid)
        assert record is not None
        assert record["id"] == rid
        assert record["title"] == "Vid"
        assert record["body"] == "Some text"

    def test_nonexistent(self, tmp_results, history):
        assert history.records.get("00000000") is None

    def test_invalid_id(self, tmp_results, history):
        assert history.records.get("not-hex!") is None


class TestResetRecord:
    def test_resets_status_and_model(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Old text")
        assert history.records.reset(rid, model="gpt-4o-transcribe-diarize") is True
        record = history.records.get(rid)
        assert record["status"] == "in_progress"
        assert record["model"] == "gpt-4o-transcribe-diarize"
        assert record["error"] == ""

    def test_keeps_the_previous_transcript_until_a_new_one_completes(self, tmp_results, history):
        """A re-transcription that never finishes must not cost the old transcript."""
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Old text")
        history.records.reset(rid, model="m2")
        assert history.records.get(rid)["body"] == "Old text"
        history.records.fail(rid, "boom")
        failed = history.records.get(rid)
        assert failed["status"] == "error"
        assert failed["body"] == "Old text"
        history.records.reset(rid)
        history.records.complete(rid, "New text")
        assert history.records.get(rid)["body"] == "New text"

    def test_refuses_a_record_that_is_already_in_progress(self, tmp_results, history):
        """Two runs must not work on one record at the same time."""
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60, model="m1")
        assert history.records.reset(rid, model="m2") is False
        assert history.records.get(rid)["model"] == "m1"

    def test_without_a_model_keeps_the_stored_one(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60, model="m1")
        history.records.complete(rid, "Text")
        assert history.records.reset(rid) is True
        assert history.records.get(rid)["model"] == "m1"

    def test_preserves_metadata(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 120)
        history.records.complete(rid, "Text")
        record_before = history.records.get(rid)
        history.records.reset(rid, model="new-model")
        record_after = history.records.get(rid)
        assert record_after["title"] == record_before["title"]
        assert record_after["url"] == record_before["url"]
        assert record_after["duration"] == record_before["duration"]
        assert record_after["created_at"] == record_before["created_at"]

    def test_nonexistent_returns_false(self, tmp_results, history):
        assert history.records.reset("00000000") is False


class TestModelField:
    def test_stored_on_create(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60, model="gpt-4o-transcribe-diarize")
        record = history.records.get(rid)
        assert record["model"] == "gpt-4o-transcribe-diarize"

    def test_preserved_through_complete(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60, model="gpt-4o-transcribe-diarize")
        history.records.complete(rid, "Text")
        record = history.records.get(rid)
        assert record["status"] == "done"
        assert record["model"] == "gpt-4o-transcribe-diarize"

    def test_preserved_through_fail(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60, model="gpt-4o-transcribe-diarize")
        history.records.fail(rid, "error msg")
        record = history.records.get(rid)
        assert record["status"] == "error"
        assert record["model"] == "gpt-4o-transcribe-diarize"

    def test_defaults_empty_for_old_records(self, tmp_results, history):
        """Records without model field should default to empty string."""
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        record = history.records.get(rid)
        assert record["model"] == ""

    def test_history_includes_model(self, tmp_results, history):
        history.records.create("Vid", "https://youtube.com/watch?v=abc", 60, model="gpt-4o-transcribe-diarize")
        records = history.records.list_all()
        assert records[0]["model"] == "gpt-4o-transcribe-diarize"


class TestDurationLimit:
    def test_stored_on_create(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 600, duration_limit=300)
        record = history.records.get(rid)
        assert record["duration_limit"] == 300

    def test_preserved_through_complete(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 600, duration_limit=300)
        history.records.complete(rid, "Text")
        record = history.records.get(rid)
        assert record["status"] == "done"
        assert record["duration_limit"] == 300

    def test_preserved_through_fail(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 600, duration_limit=300)
        history.records.fail(rid, "error msg")
        record = history.records.get(rid)
        assert record["status"] == "error"
        assert record["duration_limit"] == 300

    def test_reset_preserves_existing(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 600, duration_limit=300)
        history.records.complete(rid, "Text")
        history.records.reset(rid, model="gpt-4o-transcribe")
        record = history.records.get(rid)
        assert record["duration_limit"] == 300

    def test_reset_can_change_limit(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 600, duration_limit=300)
        history.records.complete(rid, "Text")
        history.records.reset(rid, model="gpt-4o-transcribe", duration_limit=600)
        record = history.records.get(rid)
        assert record["duration_limit"] == 600

    def test_defaults_zero_for_old_records(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        record = history.records.get(rid)
        assert record["duration_limit"] == 0

    def test_in_history(self, tmp_results, history):
        history.records.create("Vid", "https://youtube.com/watch?v=abc", 600, duration_limit=300)
        records = history.records.list_all()
        assert records[0]["duration_limit"] == 300


class TestFindCachedAudioByUrl:
    def test_finds_cached_audio(self, tmp_results, tmp_path, history):
        url = "https://youtube.com/watch?v=abc"
        rid = history.records.create("Vid", url, 120)
        history.records.complete(rid, "Transcript text")
        audio = tmp_path / "download.mp3"
        audio.write_bytes(b"\x00" * 1024)
        history.audio.save(rid, audio)
        result = history.records.find_cached_audio_for_url(url)
        assert result is not None
        cached_path, record = result
        assert cached_path.exists()
        assert record["url"] == url
        assert record["id"] == rid

    def test_returns_none_for_unknown_url(self, tmp_results, tmp_path, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 120)
        audio = tmp_path / "download.mp3"
        audio.write_bytes(b"\x00" * 1024)
        history.audio.save(rid, audio)
        assert history.records.find_cached_audio_for_url("https://youtube.com/watch?v=other") is None

    def test_returns_none_when_no_audio_cached(self, tmp_results, history):
        url = "https://youtube.com/watch?v=abc"
        history.records.create("Vid", url, 120)
        assert history.records.find_cached_audio_for_url(url) is None

    def test_returns_none_when_no_records(self, tmp_results, history):
        assert history.records.find_cached_audio_for_url("https://youtube.com/watch?v=abc") is None

    def test_finds_from_multiple_records(self, tmp_results, tmp_path, history):
        """When multiple records exist, finds the one with matching URL and audio."""
        rid1 = history.records.create("Vid 1", "https://youtube.com/watch?v=aaa", 60)
        rid2 = history.records.create("Vid 2", "https://youtube.com/watch?v=bbb", 120)
        audio = tmp_path / "download.mp3"
        audio.write_bytes(b"\x00" * 1024)
        history.audio.save(rid2, audio)
        result = history.records.find_cached_audio_for_url("https://youtube.com/watch?v=bbb")
        assert result is not None
        _, record = result
        assert record["id"] == rid2


class TestFailInProgressRecords:
    def test_only_in_progress_records_are_failed(self, tmp_results, history):
        stuck = history.records.create("Stuck", "https://youtube.com/watch?v=a", 60)
        done = history.records.create("Done", "https://youtube.com/watch?v=b", 60)
        history.records.complete(done, "text")
        assert history.records.fail_in_progress("gone") == 1
        assert history.records.get(stuck)["status"] == "error"
        assert history.records.get(stuck)["error"] == "gone"
        assert history.records.get(done)["status"] == "done"

    def test_records_still_being_worked_on_are_spared(self, tmp_results, history):
        alive = history.records.create("Alive", "https://youtube.com/watch?v=a", 60)
        dead = history.records.create("Dead", "https://youtube.com/watch?v=b", 60)
        assert history.records.fail_in_progress("stale", except_ids={alive}) == 1
        assert history.records.get(alive)["status"] == "in_progress"
        assert history.records.get(dead)["status"] == "error"


class TestDemoMarker:
    def test_real_records_are_not_demo(self, tmp_results, history):
        rid = history.records.create("Real", "https://youtube.com/watch?v=a", 60)
        assert history.records.get(rid)["demo"] is False
        assert "demo" not in history.records.file_path(rid).read_text()

    def test_marker_survives_every_status_change(self, tmp_results, history):
        rid = history.records.create("Demo", "anything", 60, demo=True)
        assert history.records.get(rid)["demo"] is True
        history.records.complete(rid, "text")
        assert history.records.get(rid)["demo"] is True
        history.records.reset(rid)
        history.records.fail(rid, "boom")
        assert history.records.get(rid)["demo"] is True
        assert history.records.list_all()[0]["demo"] is True


class TestNoPathLeak:
    def test_records_never_carry_the_file_path(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=a", 60)
        assert "path" not in history.records.get(rid)
        assert "path" not in history.records.list_all()[0]


class TestFrontmatterOrder:
    """Every status change must rewrite all fields in the canonical order."""

    def _keys(self, history, rid: str) -> list[str]:
        import yaml
        text = history.records.file_path(rid).read_text()
        return list(yaml.safe_load(text.split("---", 2)[1]).keys())

    def test_order_through_the_lifecycle(self, tmp_results, history):
        base = ["title", "url", "status", "duration", "duration_limit", "model", "created_at", "error"]
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60, model="m")
        assert self._keys(history, rid) == base
        history.records.complete(rid, "one two")
        assert self._keys(history, rid) == base[:6] + ["words"] + base[6:]
        history.records.fail(rid, "x")
        assert self._keys(history, rid) == base
        history.records.reset(rid, model="m2")
        assert self._keys(history, rid) == base
