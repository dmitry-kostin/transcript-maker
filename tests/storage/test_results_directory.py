from app.config import settings
from app.storage.results_directory import ResultsDirectory


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
