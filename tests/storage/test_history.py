from app.config import settings
from app.storage.history import History


class TestHistory:
    def test_stores_share_one_results_dir(self, tmp_results):
        history = History.in_results_dir(settings)
        rid = history.records.create("Vid", "https://youtube.com/watch?v=a", 60)
        history.records.complete(rid, "text")
        assert history.summaries.save(rid, "Summary")
        history.chunks.save(rid, "m", 2, ["part"])
        assert sorted(p.name for p in tmp_results.iterdir()) == sorted(
            [f"vid_{rid}.md", f"{rid}_summary.md", f"{rid}_chunks.json"]
        )
        assert history.records.get(rid)["has_summary"] is True
