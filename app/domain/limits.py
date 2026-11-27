"""How much audio one request may ask for. Pure rules, shared by the HTTP layer and the use cases."""

# Longest stretch of audio one transcription will process
MAX_DURATION_SECONDS = 4 * 60 * 60

# Largest "first N minutes" value a client may send — matches the frontend input's max attribute
MAX_DURATION_LIMIT_MINUTES = 480


def seconds_to_transcribe(duration: float, duration_limit: int) -> float:
    """Audio that will actually be transcribed: the whole recording, or its first ``duration_limit`` seconds."""
    if duration_limit and duration > duration_limit:
        return duration_limit
    return duration


def too_long_message(duration: float, duration_limit: int) -> str | None:
    """Why this request is refused for length, or None when it is fine.

    A "first N minutes" limit counts: the first 10 minutes of a 5-hour video are allowed.
    """
    seconds = int(seconds_to_transcribe(duration, duration_limit))
    if seconds <= MAX_DURATION_SECONDS:
        return None
    return f"Video too long ({seconds // 3600}h {(seconds % 3600) // 60}m). Max is {MAX_DURATION_SECONDS // 3600} hours."
