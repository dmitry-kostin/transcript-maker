import pytest

from app.domain.record import STATUS_DONE, STATUS_ERROR, STATUS_IN_PROGRESS, RecordId, RecordLifecycle


class TestRecordId:
    @pytest.mark.parametrize(
        "value,ok",
        [
            ("0a1b2c3d", True),
            ("0A1B2C3D", False),
            ("0a1b2c3", False),
            ("0a1b2c3d4", False),
            ("../../ab", False),
            ("", False),
        ],
    )
    def test_is_valid(self, value, ok):
        assert RecordId.is_valid(value) is ok

    def test_new_ids_are_valid(self):
        assert RecordId.is_valid(RecordId.new())

    def test_from_filename(self):
        assert RecordId.from_filename("my-video_abcd1234.md") == "abcd1234"
        assert RecordId.from_filename("no-id-here.md") is None
        assert RecordId.from_filename("abcd1234_summary.md") is None
        assert RecordId.from_filename("abcd1234_chunks.json") is None


class TestRecordLifecycle:
    @pytest.mark.parametrize("current", [STATUS_DONE, STATUS_ERROR, "unknown"])
    def test_a_record_at_rest_may_enter_any_status(self, current):
        for new in (STATUS_IN_PROGRESS, STATUS_DONE, STATUS_ERROR):
            assert RecordLifecycle.may_enter(current, new)

    def test_a_record_in_progress_cannot_be_put_in_progress_again(self):
        assert not RecordLifecycle.may_enter(STATUS_IN_PROGRESS, STATUS_IN_PROGRESS)
        assert RecordLifecycle.may_enter(STATUS_IN_PROGRESS, STATUS_DONE)
        assert RecordLifecycle.may_enter(STATUS_IN_PROGRESS, STATUS_ERROR)

    def test_is_in_progress(self):
        assert RecordLifecycle.is_in_progress({"status": STATUS_IN_PROGRESS})
        assert not RecordLifecycle.is_in_progress({"status": STATUS_DONE})

    @pytest.mark.parametrize("status,ok", [(STATUS_DONE, True), (STATUS_ERROR, False), (STATUS_IN_PROGRESS, False)])
    def test_only_a_finished_transcript_can_be_summarized(self, status, ok):
        assert RecordLifecycle.can_be_summarized({"status": status}) is ok

    def test_a_record_belongs_to_the_mode_that_made_it(self):
        assert RecordLifecycle.belongs_to_mode({"demo": True}, demo=True)
        assert RecordLifecycle.belongs_to_mode({"demo": False}, demo=False)
        assert not RecordLifecycle.belongs_to_mode({"demo": True}, demo=False)
        assert not RecordLifecycle.belongs_to_mode({"demo": False}, demo=True)
