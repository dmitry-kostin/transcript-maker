from app.config import settings
from app.wiring import Wiring


class TestWiring:
    def test_real_and_demo_are_the_same_use_cases_with_different_steps(self, tmp_results):
        wiring = Wiring(settings)
        assert type(wiring.transcription) is type(wiring.demo_transcription)
        assert type(wiring.summarization) is type(wiring.demo_summarization)
        assert wiring.transcription._tools.steps.demo is False
        assert wiring.demo_transcription._tools.steps.demo is True

    def test_both_modes_share_the_store_and_the_run_registry(self, tmp_results):
        """Cleanup must see demo runs too, and both modes list the same history."""
        wiring = Wiring(settings)
        real, demo = wiring.transcription._tools, wiring.demo_transcription._tools
        assert real.history is demo.history
        assert real.active_runs is demo.active_runs is wiring.maintenance._active_runs
