import os

os.environ.setdefault("TM_OPENAI_API_KEY", "test-key-not-real")

from pathlib import Path
from app.history import (
    _slugify, _write_md, _parse_md,
    create_record, complete_record, fail_record,
    get_history, delete_record, get_result_path,
    cleanup_stale_records,
)


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("Rick Astley - Never Gonna Give You Up!") == "rick-astley---never-gonna-give-you-up"

    def test_empty(self):
        assert _slugify("") == "untitled"

    def test_only_special_chars(self):
        assert _slugify("!!!???") == "untitled"

    def test_truncation(self):
        result = _slugify("a" * 100, max_len=10)
        assert len(result) == 10

    def test_unicode(self):
        result = _slugify("Привет мир")
        assert result  # Should produce something, not empty


class TestWriteAndParseMd:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "test_abcd1234.md"
        meta = {"title": "Test", "status": "done", "duration": "120"}
        _write_md(path, meta, "Hello transcript")
        parsed = _parse_md(path)
        assert parsed is not None
        assert parsed["id"] == "abcd1234"
        assert parsed["title"] == "Test"
        assert parsed["status"] == "done"
        assert parsed["body"] == "Hello transcript"

    def test_no_body(self, tmp_path):
        path = tmp_path / "test_abcd1234.md"
        meta = {"title": "Test", "status": "in_progress"}
        _write_md(path, meta)
        parsed = _parse_md(path)
        assert parsed is not None
        assert parsed["body"] == ""

    def test_bad_filename(self, tmp_path):
        path = tmp_path / "no-id-here.md"
        path.write_text("---\ntitle: X\n---\n")
        assert _parse_md(path) is None

    def test_missing_file(self, tmp_path):
        path = tmp_path / "missing_abcd1234.md"
        assert _parse_md(path) is None


class TestLifecycle:
    def test_create_and_list(self, tmp_results):
        rid = create_record("My Video", "https://youtube.com/watch?v=abc", 120)
        assert len(rid) == 8
        records = get_history()
        assert len(records) == 1
        assert records[0]["status"] == "in_progress"
        assert records[0]["title"] == "My Video"

    def test_complete(self, tmp_results):
        rid = create_record("Vid", "https://youtube.com/watch?v=abc", 60)
        complete_record(rid, "This is the transcript text.")
        records = get_history()
        assert records[0]["status"] == "done"

    def test_fail(self, tmp_results):
        rid = create_record("Vid", "https://youtube.com/watch?v=abc", 60)
        fail_record(rid, "API timeout")
        records = get_history()
        assert records[0]["status"] == "error"
        assert records[0]["error"] == "API timeout"

    def test_delete(self, tmp_results):
        rid = create_record("Vid", "https://youtube.com/watch?v=abc", 60)
        assert delete_record(rid) is True
        assert get_history() == []

    def test_delete_nonexistent(self, tmp_results):
        assert delete_record("00000000") is False

    def test_history_sorted_newest_first(self, tmp_results):
        import time
        rid1 = create_record("First", "https://youtube.com/watch?v=a", 10)
        time.sleep(1.1)  # created_at has second-level precision
        rid2 = create_record("Second", "https://youtube.com/watch?v=b", 20)
        records = get_history()
        assert records[0]["title"] == "Second"
        assert records[1]["title"] == "First"

    def test_get_result_path(self, tmp_results):
        rid = create_record("Vid", "https://youtube.com/watch?v=abc", 60)
        path = get_result_path(rid)
        assert path is not None
        assert path.exists()

    def test_resolve_rejects_bad_ids(self, tmp_results):
        assert get_result_path("not-hex!") is None
        assert get_result_path("") is None
        assert get_result_path("abcd12345") is None  # 9 chars
        assert get_result_path("abcd123") is None  # 7 chars

    def test_complete_nonexistent_is_safe(self, tmp_results):
        # Should not raise
        complete_record("00000000", "text")
        fail_record("00000000", "error")


class TestEdgeCases:
    def test_title_with_colons(self, tmp_results):
        rid = create_record("Live: Breaking News: Update", "https://youtube.com/watch?v=abc", 60)
        complete_record(rid, "Some transcript")
        records = get_history()
        assert records[0]["title"] == "Live: Breaking News: Update"

    def test_title_with_yaml_special_chars(self, tmp_results):
        rid = create_record("[Official] #1 Hit {2026}", "https://youtube.com/watch?v=abc", 60)
        records = get_history()
        assert records[0]["title"] == "[Official] #1 Hit {2026}"

    def test_very_long_title(self, tmp_results):
        long_title = "A" * 500
        rid = create_record(long_title, "https://youtube.com/watch?v=abc", 60)
        records = get_history()
        assert records[0]["title"] == long_title
        # Slug is truncated but file exists
        path = get_result_path(rid)
        assert path is not None

    def test_corrupt_md_skipped_in_history(self, tmp_results):
        # Create a valid record
        rid = create_record("Good", "https://youtube.com/watch?v=abc", 60)
        # Write a file with no valid ID in the filename — should be skipped
        corrupt = tmp_results / "corrupt-no-id.md"
        corrupt.write_text("this is not valid frontmatter at all")
        records = get_history()
        # Should only see the valid record, corrupt one skipped
        assert len(records) == 1
        assert records[0]["title"] == "Good"

    def test_cleanup_stale_records(self, tmp_results):
        rid1 = create_record("Stale 1", "https://youtube.com/watch?v=a", 10)
        rid2 = create_record("Stale 2", "https://youtube.com/watch?v=b", 20)
        rid3 = create_record("Done One", "https://youtube.com/watch?v=c", 30)
        complete_record(rid3, "Finished transcript")

        cleanup_stale_records()

        records = get_history()
        status_map = {r["title"]: r["status"] for r in records}
        assert status_map["Stale 1"] == "error"
        assert status_map["Stale 2"] == "error"
        assert status_map["Done One"] == "done"
