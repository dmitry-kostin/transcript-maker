from app.usecases.active_runs import ActiveRuns


class TestActiveRuns:
    def test_nothing_runs_at_first(self):
        runs = ActiveRuns()
        assert not runs.any_active()
        assert runs.record_ids() == set()

    def test_a_run_is_active_from_start_to_finish(self):
        runs = ActiveRuns()
        run = runs.start()
        assert runs.any_active()
        assert runs.record_ids() == set()  # downloading: no record yet
        run.record_id = "abcd1234"
        assert runs.record_ids() == {"abcd1234"}
        runs.finish(run)
        assert not runs.any_active()

    def test_runs_are_tracked_separately(self):
        runs = ActiveRuns()
        first, second = runs.start(), runs.start()
        first.record_id, second.record_id = "aaaaaaaa", "bbbbbbbb"
        runs.finish(first)
        assert runs.record_ids() == {"bbbbbbbb"}

    def test_finishing_twice_is_harmless(self):
        runs = ActiveRuns()
        run = runs.start()
        runs.finish(run)
        runs.finish(run)
        assert not runs.any_active()
