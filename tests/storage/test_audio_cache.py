

class TestAudioCache:
    def test_save_and_find(self, tmp_results, tmp_path, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        audio = tmp_path / "download.mp3"
        audio.write_bytes(b"\x00" * 1024)
        cached = history.audio.save(rid, audio)
        assert cached is not None
        assert cached.exists()
        assert cached.name == f"{rid}.mp3"
        found = history.audio.path_for(rid)
        assert found is not None
        assert found == cached

    def test_get_audio_nonexistent(self, tmp_results, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        assert history.audio.path_for(rid) is None

    def test_get_audio_invalid_id(self, tmp_results, history):
        assert history.audio.path_for("not-hex!") is None
        assert history.audio.path_for("") is None
        assert history.audio.path_for("abcd12345") is None

    def test_delete_removes_audio(self, tmp_results, tmp_path, history):
        rid = history.records.create("Vid", "https://youtube.com/watch?v=abc", 60)
        audio = tmp_path / "download.mp3"
        audio.write_bytes(b"\x00" * 1024)
        cached = history.audio.save(rid, audio)
        assert cached.exists()
        history.records.delete(rid)
        assert not cached.exists()
        assert history.audio.path_for(rid) is None
