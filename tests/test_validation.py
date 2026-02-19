import pytest
from pydantic import ValidationError
from app.api import TranscribeRequest


class TestTranscribeRequestValidation:
    """Test backend URL validation via pydantic field_validator."""

    def test_youtube_com(self):
        req = TranscribeRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert req.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_youtube_no_www(self):
        req = TranscribeRequest(url="https://youtube.com/watch?v=dQw4w9WgXcQ")
        assert "youtube.com" in req.url

    def test_mobile_youtube(self):
        req = TranscribeRequest(url="https://m.youtube.com/watch?v=dQw4w9WgXcQ")
        assert req.url.startswith("https://m.youtube.com")

    def test_youtu_be(self):
        req = TranscribeRequest(url="https://youtu.be/dQw4w9WgXcQ")
        assert req.url == "https://youtu.be/dQw4w9WgXcQ"

    def test_youtube_with_si_param(self):
        req = TranscribeRequest(url="https://www.youtube.com/watch?si=abc123&v=dQw4w9WgXcQ")
        assert "v=dQw4w9WgXcQ" in req.url

    def test_youtube_shorts(self):
        req = TranscribeRequest(url="https://www.youtube.com/shorts/dQw4w9WgXcQ")
        assert "shorts" in req.url

    def test_rejects_vimeo(self):
        with pytest.raises(ValidationError, match="YouTube"):
            TranscribeRequest(url="https://vimeo.com/123456")

    def test_rejects_random_url(self):
        with pytest.raises(ValidationError, match="YouTube"):
            TranscribeRequest(url="https://example.com/video")

    def test_rejects_empty(self):
        # Empty string is technically a valid string but invalid URL
        with pytest.raises(ValidationError):
            TranscribeRequest(url="")

    def test_rejects_non_url(self):
        with pytest.raises(ValidationError):
            TranscribeRequest(url="not a url at all")

    def test_rejects_playlist_path(self):
        with pytest.raises(ValidationError, match="Playlist URLs are not supported"):
            TranscribeRequest(url="https://www.youtube.com/playlist?list=PLtxgRxNe7rCz")

    def test_rejects_list_param_without_video(self):
        with pytest.raises(ValidationError, match="Playlist URLs are not supported"):
            TranscribeRequest(url="https://www.youtube.com/watch?list=PLtxgRxNe7rCz")

    def test_allows_video_with_list_param(self):
        req = TranscribeRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLtxgRxNe7rCz")
        assert "v=dQw4w9WgXcQ" in req.url

    def test_allows_video_with_list_and_index(self):
        req = TranscribeRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLtxgRxNe7rCz&index=5")
        assert "v=dQw4w9WgXcQ" in req.url

    def test_allows_youtu_be_with_list_param(self):
        req = TranscribeRequest(url="https://youtu.be/dQw4w9WgXcQ?list=PLtxgRxNe7rCz")
        assert "youtu.be" in req.url
