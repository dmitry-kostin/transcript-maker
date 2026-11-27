"""Request bodies and their validation."""
from typing import Annotated
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, field_validator

from app.domain.limits import MAX_DURATION_LIMIT_MINUTES

ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

# "First N minutes" as the client sends it: 0 = no limit
DurationLimitMinutes = Annotated[int, Field(ge=0, le=MAX_DURATION_LIMIT_MINUTES)]


class TranscriptionOptions(BaseModel):
    """What every transcription request may choose. Unknown fields (e.g. the removed ``diarize``) are ignored."""

    model: str = ""
    duration_limit: DurationLimitMinutes = 0

    @property
    def duration_limit_seconds(self) -> int:
        """The use cases and stored records work in seconds."""
        return self.duration_limit * 60


class TranscribeRequest(TranscriptionOptions):
    url: str

    @field_validator("url")
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError("URL must be a YouTube link")
        qs = parse_qs(parsed.query)
        has_video_id = "v" in qs or (
            parsed.hostname == "youtu.be" and len(parsed.path) > 1
        )
        if parsed.path == "/playlist" or ("list" in qs and not has_video_id):
            raise ValueError(
                "Playlist URLs are not supported. Please provide a single video URL."
            )
        return v


class DemoTranscribeRequest(TranscriptionOptions):
    """Demo mode accepts anything as a URL."""

    url: str = "demo"


class RetranscribeRequest(TranscriptionOptions):
    pass


class SummarizeRequest(BaseModel):
    prompt: str = ""
    model: str = ""
