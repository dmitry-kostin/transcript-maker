from app.domain.limits import MAX_DURATION_SECONDS, DurationLimits

limits = DurationLimits()


class TestDurationLimits:
    def test_whole_recording_when_no_limit(self):
        assert limits.seconds_to_transcribe(900, 0) == 900

    def test_limit_shortens_but_never_lengthens(self):
        assert limits.seconds_to_transcribe(900, 300) == 300
        assert limits.seconds_to_transcribe(200, 300) == 200

    def test_too_long_is_refused(self):
        assert "Max is 4 hours" in limits.too_long_message(MAX_DURATION_SECONDS + 60, 0)

    def test_exactly_the_maximum_is_fine(self):
        assert limits.too_long_message(MAX_DURATION_SECONDS, 0) is None

    def test_first_minutes_of_a_long_video_are_fine(self):
        assert limits.too_long_message(5 * 60 * 60, 600) is None

    def test_maximum_is_configurable(self):
        assert "Max is 1 hours" in DurationLimits(max_seconds=3600).too_long_message(2 * 3600, 0)
