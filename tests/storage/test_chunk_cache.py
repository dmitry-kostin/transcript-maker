class TestChunkCache:
    def test_save_and_load(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        parts = ["chunk 0 text", "chunk 1 text"]
        history.chunks.save(rid, "gemini-3-flash-preview", 5, parts)
        loaded = history.chunks.load(rid, "gemini-3-flash-preview", 5)
        assert loaded == parts

    def test_load_missing_returns_empty(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        assert history.chunks.load(rid, "gemini-3-flash-preview", 5) == []

    def test_invalidated_by_model_change(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.chunks.save(rid, "gemini-3-flash-preview", 5, ["chunk 0"])
        assert history.chunks.load(rid, "gpt-4o-transcribe", 5) == []

    def test_invalidated_by_total_change(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.chunks.save(rid, "gemini-3-flash-preview", 5, ["chunk 0"])
        assert history.chunks.load(rid, "gemini-3-flash-preview", 3) == []

    def test_delete(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.chunks.save(rid, "gemini-3-flash-preview", 5, ["chunk 0"])
        history.chunks.delete(rid)
        assert history.chunks.load(rid, "gemini-3-flash-preview", 5) == []

    def test_delete_nonexistent_is_safe(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.chunks.delete(rid)  # Should not raise

    def test_delete_invalid_id_is_safe(self, tmp_results, history):
        history.chunks.delete("not-hex!")  # Should not raise

    def test_delete_record_cascades_to_chunk_cache(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.chunks.save(rid, "gemini-3-flash-preview", 5, ["chunk 0"])
        history.records.delete(rid)
        assert history.chunks.load(rid, "gemini-3-flash-preview", 5) == []

    def test_chunk_cache_not_in_history_glob(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.chunks.save(rid, "gemini-3-flash-preview", 5, ["chunk 0"])
        records = history.records.list_all()
        assert len(records) == 1
        assert records[0]["id"] == rid

    def test_incremental_save(self, tmp_results, history):
        """Simulate saving after each chunk completes."""
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        history.chunks.save(rid, "model", 3, ["part0"])
        history.chunks.save(rid, "model", 3, ["part0", "part1"])
        history.chunks.save(rid, "model", 3, ["part0", "part1", "part2"])
        loaded = history.chunks.load(rid, "model", 3)
        assert loaded == ["part0", "part1", "part2"]

    def test_corrupt_json_returns_empty(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        cache_path = tmp_results / f"{rid}_chunks.json"
        cache_path.write_text("not valid json", encoding="utf-8")
        assert history.chunks.load(rid, "model", 5) == []


class TestChunkCacheDurationLimit:
    def test_a_different_duration_limit_invalidates_the_cache(self, tmp_results, history):
        """Same model, same chunk count, but the last chunk is cut somewhere else."""
        rid = history.records.create("Test", "https://youtube.com/watch?v=abc", 1500)
        history.chunks.save(rid, "model", 3, ["a", "b", "c"], duration_limit=0)
        assert history.chunks.load(rid, "model", 3, duration_limit=1260) == []
        assert history.chunks.load(rid, "model", 3, duration_limit=0) == ["a", "b", "c"]
