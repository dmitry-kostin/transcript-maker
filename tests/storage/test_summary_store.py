

class TestSummaryCRUD:
    def test_save_and_get(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Some transcript text")
        assert history.summaries.save(rid, "This is the summary", "Custom prompt") is True
        result = history.summaries.get(rid)
        assert result is not None
        assert result["summary"] == "This is the summary"
        assert result["prompt"] == "Custom prompt"
        assert result["created_at"] != ""

    def test_get_nonexistent(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        assert history.summaries.get(rid) is None

    def test_get_invalid_id(self, tmp_results, history):
        assert history.summaries.get("not-hex!") is None

    def test_save_for_nonexistent_record(self, tmp_results, history):
        assert history.summaries.save("00000000", "Summary text") is False

    def test_delete_summary(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Some text")
        history.summaries.save(rid, "Summary")
        assert history.summaries.get(rid) is not None
        history.summaries.delete(rid)
        assert history.summaries.get(rid) is None

    def test_delete_record_removes_summary(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Some text")
        history.summaries.save(rid, "Summary")
        assert history.summaries.get(rid) is not None
        history.records.delete(rid)
        assert history.summaries.get(rid) is None

    def test_overwrite_summary(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Some text")
        history.summaries.save(rid, "First summary", "Prompt 1")
        history.summaries.save(rid, "Second summary", "Prompt 2")
        result = history.summaries.get(rid)
        assert result["summary"] == "Second summary"
        assert result["prompt"] == "Prompt 2"

    def test_summary_not_in_history_glob(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Some text")
        history.summaries.save(rid, "Summary")
        records = history.records.list_all()
        # Only the real record should appear, not the summary sidecar
        assert len(records) == 1
        assert records[0]["id"] == rid

    def test_has_summary_flag_in_history(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Some text")
        records = history.records.list_all()
        assert records[0]["has_summary"] is False
        history.summaries.save(rid, "Summary")
        records = history.records.list_all()
        assert records[0]["has_summary"] is True

    def test_has_summary_flag_in_get_record(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.records.complete(rid, "Some text")
        record = history.records.get(rid)
        assert record["has_summary"] is False
        history.summaries.save(rid, "Summary")
        record = history.records.get(rid)
        assert record["has_summary"] is True
