from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.services.chunker import AudioChunker


class TestPrepareChunksSmallFile:
    """When file is under the size limit, no chunking should happen."""

    def test_small_file_returns_single_path(self, tmp_path, monkeypatch):
        # Create a file under the 24MB limit (1 MB)
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))

        # Patch settings to use 24MB limit
        monkeypatch.setattr(settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr(AudioChunker, "duration", lambda self, path: 600.0)

        result = AudioChunker(settings).prepare_chunks(audio)
        assert result == [audio]


class TestPrepareChunksLargeFile:
    """When file exceeds the size limit, it should be split via ffmpeg."""

    def test_calls_ffmpeg_for_large_file(self, tmp_path, monkeypatch):
        # Create a 30 MB file (over 24MB limit)
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (30 * 1024 * 1024))

        monkeypatch.setattr(settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr(settings, "audio_format", "mp3")

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

        monkeypatch.setattr("app.services.chunker.subprocess.run", mock_subprocess_run)

        result = AudioChunker(settings).prepare_chunks(audio)
        assert len(result) == 2  # 30MB / 24MB limit ≈ 2 chunks

    def test_raises_on_oversized_chunk(self, tmp_path, monkeypatch):
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (30 * 1024 * 1024))

        monkeypatch.setattr(settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr(settings, "audio_format", "mp3")

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

        monkeypatch.setattr("app.services.chunker.subprocess.run", mock_subprocess_run)

        with pytest.raises(RuntimeError, match="exceeds"):
            AudioChunker(settings).prepare_chunks(audio)


class TestDurationLimit:
    """When duration_limit is set, audio should be trimmed before chunking."""

    def test_trimmed_when_duration_exceeds_limit(self, tmp_path, monkeypatch):
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))

        monkeypatch.setattr(settings, "max_chunk_size_mb", 24.0)

        ffmpeg_calls = []

        def mock_subprocess_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                result = MagicMock()
                result.stdout = '{"format": {"duration": "600.0"}}'
                return result
            if cmd[0] == "ffmpeg":
                ffmpeg_calls.append(cmd)
                output_path = Path(cmd[-1])
                output_path.write_bytes(b"\x00" * (512 * 1024))
                return MagicMock()
            return MagicMock()

        monkeypatch.setattr("app.services.chunker.subprocess.run", mock_subprocess_run)

        result = AudioChunker(settings).prepare_chunks(audio, duration_limit=300)
        assert len(result) == 1
        # Verify ffmpeg was called with -t 300 for trimming
        assert len(ffmpeg_calls) == 1
        trim_call = ffmpeg_calls[0]
        assert "-t" in trim_call
        t_idx = trim_call.index("-t")
        assert trim_call[t_idx + 1] == "300"
        # Trimmed file should be used
        assert "_trimmed" in str(result[0])

    def test_no_trim_when_duration_under_limit(self, tmp_path, monkeypatch):
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))

        monkeypatch.setattr(settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr(AudioChunker, "duration", lambda self, path: 200.0)

        result = AudioChunker(settings).prepare_chunks(audio, duration_limit=300)
        assert result == [audio]

    def test_no_trim_when_limit_is_none(self, tmp_path, monkeypatch):
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))

        monkeypatch.setattr(settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr(AudioChunker, "duration", lambda self, path: 600.0)

        result = AudioChunker(settings).prepare_chunks(audio, duration_limit=None)
        assert result == [audio]


class TestPrepareChunksByDurationAndSize:
    def test_small_file_short_duration_no_chunking(self, tmp_path):
        """File under size AND duration limits → no chunking."""
        audio = tmp_path / "short.mp3"
        audio.write_bytes(b"\x00" * 1_000_000)  # ~1 MB
        with patch.object(AudioChunker, "duration", return_value=600.0):
            chunks = AudioChunker(settings).prepare_chunks(audio)
        assert chunks == [audio]

    def test_small_file_long_duration_triggers_chunking(self, tmp_path):
        """File under size limit but over duration limit → must chunk."""
        audio = tmp_path / "long.mp3"
        audio.write_bytes(b"\x00" * 5_000_000)  # ~5 MB, well under 24 MB
        long_duration = 3000.0  # 50 minutes, way over 600s

        # 3000s / 600s = 5 chunks
        for i in range(5):
            (tmp_path / f"long_chunk{i}.mp3").write_bytes(b"\x00" * 1_000_000)

        call_count = 0

        def fake_ffmpeg(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MagicMock(returncode=0)

        with patch.object(AudioChunker, "duration", return_value=long_duration), \
             patch("app.services.chunker.subprocess.run", side_effect=fake_ffmpeg):
            chunks = AudioChunker(settings).prepare_chunks(audio)

        assert len(chunks) == 5  # 3000s / 600s = 5 chunks

    def test_large_file_short_duration_triggers_chunking(self, tmp_path):
        """File over size limit but short duration → chunk by size."""
        audio = tmp_path / "big.mp3"
        audio.write_bytes(b"\x00" * 30_000_000)  # ~30 MB, over 24 MB limit

        chunk_paths = [tmp_path / "big_chunk0.mp3", tmp_path / "big_chunk1.mp3"]
        for p in chunk_paths:
            p.write_bytes(b"\x00" * 12_000_000)

        call_count = 0

        def fake_ffmpeg(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MagicMock(returncode=0)

        with patch.object(AudioChunker, "duration", return_value=600.0), \
             patch("app.services.chunker.subprocess.run", side_effect=fake_ffmpeg):
            chunks = AudioChunker(settings).prepare_chunks(audio)

        assert len(chunks) >= 2


class TestDuration:
    def test_reads_ffprobe_output(self, tmp_path, monkeypatch):
        probe = MagicMock(stdout='{"format": {"duration": "123.5"}}')
        monkeypatch.setattr("app.services.chunker.subprocess.run", lambda cmd, **kwargs: probe)
        assert AudioChunker(settings).duration(tmp_path / "a.mp3") == 123.5

    @pytest.mark.parametrize("stdout", ["", "not json", "{}", '{"format": {"duration": "n/a"}}'])
    def test_unexpected_ffprobe_output_is_a_clear_error(self, tmp_path, monkeypatch, stdout):
        probe = MagicMock(stdout=stdout)
        monkeypatch.setattr("app.services.chunker.subprocess.run", lambda cmd, **kwargs: probe)
        with pytest.raises(RuntimeError, match="Could not read audio metadata"):
            AudioChunker(settings).duration(tmp_path / "a.mp3")
