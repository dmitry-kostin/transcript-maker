from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from app.transcriber import prepare_chunks, cleanup_temp_files, MAX_UPLOAD_SIZE_MB


class TestPrepareChunksSmallFile:
    """When file is under the size limit, no chunking should happen."""

    def test_small_file_returns_single_path(self, tmp_path, monkeypatch):
        # Create a file under the 24MB limit (1 MB)
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))

        # Patch settings to use 24MB limit
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "max_chunk_size_mb", 24.0)

        result = prepare_chunks(audio)
        assert result == [audio]


class TestPrepareChunksLargeFile:
    """When file exceeds the size limit, it should be split via ffmpeg."""

    def test_calls_ffmpeg_for_large_file(self, tmp_path, monkeypatch):
        # Create a 30 MB file (over 24MB limit)
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (30 * 1024 * 1024))

        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr(mod.settings, "audio_format", "mp3")

        # Mock ffprobe to return 600s duration
        mock_ffprobe = MagicMock()
        mock_ffprobe.stdout = '{"format": {"duration": "600.0"}}'

        # Mock ffmpeg to create chunk files of 20MB each
        def mock_subprocess_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return mock_ffprobe
            if cmd[0] == "ffmpeg":
                # Create the output file (the last argument)
                output_path = Path(cmd[-1])
                output_path.write_bytes(b"\x00" * (20 * 1024 * 1024))
                return MagicMock()
            return MagicMock()

        monkeypatch.setattr("app.transcriber.subprocess.run", mock_subprocess_run)

        result = prepare_chunks(audio)
        assert len(result) == 2  # 30MB / 24MB limit ≈ 2 chunks

    def test_raises_on_oversized_chunk(self, tmp_path, monkeypatch):
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (30 * 1024 * 1024))

        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr(mod.settings, "audio_format", "mp3")

        mock_ffprobe = MagicMock()
        mock_ffprobe.stdout = '{"format": {"duration": "600.0"}}'

        def mock_subprocess_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return mock_ffprobe
            if cmd[0] == "ffmpeg":
                # Create an oversized chunk (26MB > 25MB limit)
                output_path = Path(cmd[-1])
                output_path.write_bytes(b"\x00" * (26 * 1024 * 1024))
                return MagicMock()
            return MagicMock()

        monkeypatch.setattr("app.transcriber.subprocess.run", mock_subprocess_run)

        with pytest.raises(RuntimeError, match="exceeds"):
            prepare_chunks(audio)


class TestCleanupTempFiles:
    def test_removes_matching_files(self, tmp_path):
        base = tmp_path / "video_abc12345.mp3"
        chunk0 = tmp_path / "video_abc12345_chunk0.mp3"
        chunk1 = tmp_path / "video_abc12345_chunk1.mp3"
        unrelated = tmp_path / "other_file.mp3"

        for f in [base, chunk0, chunk1, unrelated]:
            f.write_bytes(b"\x00")

        cleanup_temp_files(base)

        assert not base.exists()
        assert not chunk0.exists()
        assert not chunk1.exists()
        assert unrelated.exists()  # Should NOT be deleted
