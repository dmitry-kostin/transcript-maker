import pytest

from app.config import settings
from app.storage.results_directory import RecordId, ResultsDirectory


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


class TestResultsDirectory:
    def test_follows_the_settings(self, tmp_results):
        assert ResultsDirectory(settings).path == tmp_results

    def test_record_file(self, tmp_results):
        directory = ResultsDirectory(settings)
        file = directory.new_record_file("my-video", "abcd1234")
        file.write_text("x")
        assert directory.record_file("abcd1234") == file.resolve()
        assert directory.record_file("00000000") is None
        assert directory.record_file("../../ab") is None

    def test_audio_is_any_file_named_by_the_id_except_markdown(self, tmp_results):
        directory = ResultsDirectory(settings)
        assert directory.existing_audio_file("abcd1234") is None
        directory.audio_file("abcd1234", ".mp3").write_bytes(b"\x00")
        assert directory.existing_audio_file("abcd1234") == tmp_results / "abcd1234.mp3"
        assert directory.existing_audio_file("not-hex!") is None
