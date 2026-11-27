from app.config import settings
from app.services.temp_files import TempDirectory


class TestRemoveRunFiles:
    def test_removes_the_audio_and_everything_cut_from_it(self, tmp_path):
        base = tmp_path / "video_abc12345.mp3"
        chunk0 = tmp_path / "video_abc12345_chunk0.mp3"
        trimmed = tmp_path / "video_abc12345_trimmed.mp3"
        unrelated = tmp_path / "other_file.mp3"
        for file in [base, chunk0, trimmed, unrelated]:
            file.write_bytes(b"\x00")

        TempDirectory(settings).remove_run_files(base)

        assert not base.exists()
        assert not chunk0.exists()
        assert not trimmed.exists()
        assert unrelated.exists()


class TestPurge:
    def test_deletes_files_but_not_subdirectories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
        (tmp_path / "a.mp3").write_bytes(b"\x00")
        (tmp_path / "b.webm.part").write_bytes(b"\x00")
        kept = tmp_path / "test_cache" / "audio.mp3"
        kept.parent.mkdir()
        kept.write_bytes(b"\x00")

        assert TempDirectory(settings).purge() == 2
        assert kept.exists()

    def test_missing_directory_is_nothing_to_purge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path / "nope"))
        assert TempDirectory(settings).purge() == 0


class TestCopyIn:
    def test_copies_under_a_unique_name_and_leaves_the_original(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path / "tmp"))
        source = tmp_path / "cached.mp3"
        source.write_bytes(b"audio")
        temp = TempDirectory(settings)

        first, second = temp.copy_in(source, "cached"), temp.copy_in(source, "cached")

        assert first != second
        assert first.parent == tmp_path / "tmp"
        assert first.name.startswith("cached_") and first.suffix == ".mp3"
        assert first.read_bytes() == b"audio"
        assert source.exists()
