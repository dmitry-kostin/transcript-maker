from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from app.clients import is_gemini_model, get_client
from app.transcriber import (
    prepare_chunks, cleanup_temp_files, MAX_UPLOAD_SIZE_MB,
    resolve_model, get_stored_model,
    transcribe_chunk, _transcribe_base, _transcribe_diarize, _transcribe_gemini,
)


class TestPrepareChunksSmallFile:
    """When file is under the size limit, no chunking should happen."""

    def test_small_file_returns_single_path(self, tmp_path, monkeypatch):
        # Create a file under the 24MB limit (1 MB)
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))

        # Patch settings to use 24MB limit
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr("app.transcriber._get_duration", lambda _: 600.0)

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


class TestDurationLimit:
    """When duration_limit is set, audio should be trimmed before chunking."""

    def test_trimmed_when_duration_exceeds_limit(self, tmp_path, monkeypatch):
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))

        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "max_chunk_size_mb", 24.0)

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

        monkeypatch.setattr("app.transcriber.subprocess.run", mock_subprocess_run)

        result = prepare_chunks(audio, duration_limit=300)
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

        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr("app.transcriber._get_duration", lambda _: 200.0)

        result = prepare_chunks(audio, duration_limit=300)
        assert result == [audio]

    def test_no_trim_when_limit_is_none(self, tmp_path, monkeypatch):
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))

        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "max_chunk_size_mb", 24.0)
        monkeypatch.setattr("app.transcriber._get_duration", lambda _: 600.0)

        result = prepare_chunks(audio, duration_limit=None)
        assert result == [audio]


class TestIsGeminiModel:
    def test_gemini_model(self):
        assert is_gemini_model("gemini-3-flash-preview") is True

    def test_gemini_other(self):
        assert is_gemini_model("gemini-2.0-flash") is True

    def test_openai_model(self):
        assert is_gemini_model("gpt-4o-transcribe") is False

    def test_empty_string(self):
        assert is_gemini_model("") is False


class TestResolveModel:
    def test_empty_uses_default(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        assert resolve_model("") == ("gpt-4o-transcribe", False)

    def test_empty_uses_gemini_default(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-3-flash-preview")
        assert resolve_model("") == ("gemini-3-flash-preview", False)

    def test_diarize_suffix_extracts_base_model(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        assert resolve_model("gpt-4o-transcribe-diarize") == ("gpt-4o-transcribe", True)

    def test_diarize_suffix_preserves_explicit_model(self, monkeypatch):
        """With Gemini default, explicit OpenAI-diarize preserves the explicit model."""
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-3-flash-preview")
        assert resolve_model("gpt-4o-transcribe-diarize") == ("gpt-4o-transcribe", True)

    def test_gemini_diarize_suffix(self):
        assert resolve_model("gemini-2.0-flash-diarize") == ("gemini-2.0-flash", True)

    def test_explicit_model(self):
        assert resolve_model("gpt-4o-transcribe") == ("gpt-4o-transcribe", False)

    def test_explicit_gemini(self):
        assert resolve_model("gemini-3-flash-preview") == ("gemini-3-flash-preview", False)

    def test_diarize_param_with_empty_model(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        assert resolve_model("", diarize=True) == ("gpt-4o-transcribe", True)

    def test_diarize_param_with_gemini_default(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-2.0-flash")
        assert resolve_model("", diarize=True) == ("gemini-2.0-flash", True)

    def test_diarize_param_with_explicit_model(self):
        assert resolve_model("gemini-2.0-flash", diarize=True) == ("gemini-2.0-flash", True)

    def test_suffix_overrides_diarize_false(self):
        """Suffix -diarize wins even when diarize param is False."""
        assert resolve_model("gpt-4o-transcribe-diarize", diarize=False) == ("gpt-4o-transcribe", True)

    def test_bare_diarize_suffix_uses_default(self, monkeypatch):
        """Model string '-diarize' with empty base falls back to settings default."""
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        assert resolve_model("-diarize") == ("gpt-4o-transcribe", True)


class TestGetStoredModel:
    def test_empty_request_openai(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        assert get_stored_model("") == "gpt-4o-transcribe"

    def test_empty_request_gemini(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-3-flash-preview")
        assert get_stored_model("") == "gemini-3-flash-preview"

    def test_diarize_suffix_openai(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        assert get_stored_model("gpt-4o-transcribe-diarize") == "gpt-4o-transcribe-diarize"

    def test_diarize_suffix_preserves_explicit_model(self, monkeypatch):
        """Explicit model in suffix is preserved, not replaced by settings default."""
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-3-flash-preview")
        assert get_stored_model("gpt-4o-transcribe-diarize") == "gpt-4o-transcribe-diarize"

    def test_diarize_param_openai(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        assert get_stored_model("", diarize=True) == "gpt-4o-transcribe-diarize"

    def test_diarize_param_gemini(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-3-flash-preview")
        assert get_stored_model("", diarize=True) == "gemini-3-flash-preview-diarize"

    def test_explicit_model(self):
        assert get_stored_model("gpt-4o-transcribe") == "gpt-4o-transcribe"


class TestGetClient:
    def test_openai_client(self):
        client = get_client("gpt-4o-transcribe")
        assert client.base_url.host == "api.openai.com"

    def test_gemini_client_with_key(self, monkeypatch):
        import app.clients as mod
        monkeypatch.setattr(mod.settings, "google_api_key", "test-key")
        client = get_client("gemini-3-flash-preview")
        assert "generativelanguage.googleapis.com" in str(client.base_url)

    def test_gemini_client_missing_key(self, monkeypatch):
        import app.clients as mod
        monkeypatch.setattr(mod.settings, "google_api_key", "")
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            get_client("gemini-3-flash-preview")


class TestTranscribeChunkRouting:
    """Verify transcribe_chunk dispatches to the correct backend."""

    @pytest.fixture(autouse=True)
    def _chunk(self, tmp_path):
        self.chunk = tmp_path / "audio.mp3"
        self.chunk.write_bytes(b"\x00" * 1024)

    @pytest.mark.asyncio
    async def test_default_openai_calls_base(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        mock = AsyncMock(return_value="hello")
        with patch("app.transcriber._transcribe_base", mock):
            result = await transcribe_chunk(self.chunk, model="gpt-4o-transcribe")
        mock.assert_awaited_once_with(self.chunk, "gpt-4o-transcribe")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_diarize_calls_diarize(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        mock = AsyncMock(return_value="Speaker 1: hi")
        with patch("app.transcriber._transcribe_diarize", mock):
            result = await transcribe_chunk(self.chunk, model="gpt-4o-transcribe", diarize=True)
        mock.assert_awaited_once_with(self.chunk, "gpt-4o-transcribe-diarize")
        assert result == "Speaker 1: hi"

    @pytest.mark.asyncio
    async def test_gemini_default_calls_gemini(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-3-flash-preview")
        mock = AsyncMock(return_value="gemini text")
        with patch("app.transcriber._transcribe_gemini", mock):
            result = await transcribe_chunk(self.chunk, model="gemini-3-flash-preview")
        mock.assert_awaited_once_with(self.chunk, "gemini-3-flash-preview", False)
        assert result == "gemini text"

    @pytest.mark.asyncio
    async def test_diarize_with_gemini(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gemini-3-flash-preview")
        mock = AsyncMock(return_value="Speaker 1: gemini")
        with patch("app.transcriber._transcribe_gemini", mock):
            result = await transcribe_chunk(self.chunk, model="gemini-3-flash-preview", diarize=True)
        mock.assert_awaited_once_with(self.chunk, "gemini-3-flash-preview", True)
        assert result == "Speaker 1: gemini"

    @pytest.mark.asyncio
    async def test_empty_model_uses_default(self, monkeypatch):
        import app.transcriber as mod
        monkeypatch.setattr(mod.settings, "transcribe_model", "gpt-4o-transcribe")
        mock = AsyncMock(return_value="hello")
        with patch("app.transcriber._transcribe_base", mock):
            result = await transcribe_chunk(self.chunk, model="")
        mock.assert_awaited_once_with(self.chunk, "gpt-4o-transcribe")
        assert result == "hello"


class TestTranscribeGemini:
    """Test _transcribe_gemini with a mocked client."""

    @pytest.fixture(autouse=True)
    def _chunk(self, tmp_path):
        self.chunk = tmp_path / "audio.mp3"
        self.chunk.write_bytes(b"\xff\xd8" * 100)

    def _mock_client(self, response_text: str) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.content = f"  {response_text}  "
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        return mock_client

    @pytest.mark.asyncio
    async def test_non_diarize_prompt(self):
        client = self._mock_client("Hello world")
        with patch("app.transcriber.get_client", return_value=client):
            result = await _transcribe_gemini(self.chunk, "gemini-3-flash-preview", diarize=False)
        assert result == "Hello world"
        call_kwargs = client.chat.completions.create.call_args.kwargs
        prompt_text = call_kwargs["messages"][0]["content"][0]["text"]
        assert "verbatim" in prompt_text

    @pytest.mark.asyncio
    async def test_diarize_prompt(self):
        client = self._mock_client("Speaker 1: Hi")
        with patch("app.transcriber.get_client", return_value=client):
            result = await _transcribe_gemini(self.chunk, "gemini-3-flash-preview", diarize=True)
        assert result == "Speaker 1: Hi"
        call_kwargs = client.chat.completions.create.call_args.kwargs
        prompt_text = call_kwargs["messages"][0]["content"][0]["text"]
        assert "'A: text'" in prompt_text

    @pytest.mark.asyncio
    async def test_sends_base64_audio(self):
        client = self._mock_client("text")
        with patch("app.transcriber.get_client", return_value=client):
            await _transcribe_gemini(self.chunk, "gemini-3-flash-preview", diarize=False)
        call_kwargs = client.chat.completions.create.call_args.kwargs
        audio_part = call_kwargs["messages"][0]["content"][1]
        assert audio_part["type"] == "input_audio"
        assert len(audio_part["input_audio"]["data"]) > 0


class TestTranscribeBase:
    """Test _transcribe_base with a mocked client."""

    @pytest.fixture(autouse=True)
    def _chunk(self, tmp_path):
        self.chunk = tmp_path / "audio.mp3"
        self.chunk.write_bytes(b"\x00" * 1024)

    @pytest.mark.asyncio
    async def test_returns_stripped_text(self):
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create = AsyncMock(return_value="  Hello world  ")
        with patch("app.transcriber.get_client", return_value=mock_client):
            result = await _transcribe_base(self.chunk, "gpt-4o-transcribe")
        assert result == "Hello world"


class TestTranscribeDiarize:
    """Test _transcribe_diarize with a mocked client."""

    @pytest.fixture(autouse=True)
    def _chunk(self, tmp_path):
        self.chunk = tmp_path / "audio.mp3"
        self.chunk.write_bytes(b"\x00" * 1024)

    @pytest.mark.asyncio
    async def test_joins_speaker_segments(self):
        seg1 = MagicMock()
        seg1.__dict__ = {"speaker": "Speaker 1", "text": " Hello "}
        seg2 = MagicMock()
        seg2.__dict__ = {"speaker": "Speaker 2", "text": " World "}
        mock_resp = MagicMock()
        mock_resp.segments = [seg1, seg2]
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_resp)
        with patch("app.transcriber.get_client", return_value=mock_client):
            result = await _transcribe_diarize(self.chunk, "gpt-4o-transcribe-diarize")
        assert result == "Speaker 1: Hello\nSpeaker 2: World"


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

    def test_trimmed_file_cleaned_up(self, tmp_path):
        """Trimmed file shares the same stem prefix, so cleanup catches it."""
        base = tmp_path / "audio_abc12345.mp3"
        trimmed = tmp_path / "audio_abc12345_trimmed.mp3"
        for f in [base, trimmed]:
            f.write_bytes(b"\x00")

        cleanup_temp_files(base)

        assert not base.exists()
        assert not trimmed.exists()
