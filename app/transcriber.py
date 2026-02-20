import base64
import json
import logging
import subprocess
import warnings
from pathlib import Path

from app.clients import is_gemini_model, get_client
from app.config import settings

logger = logging.getLogger(__name__)

MAX_UPLOAD_SIZE_MB = 25.0
MAX_CHUNK_DURATION_SECONDS = 1200  # Whisper API rejects >1400s; use 1200s safety margin


def resolve_model(requested: str, diarize: bool = False) -> tuple[str, bool]:
    """Resolve a requested model string into (actual_model, diarize_flag).

    - "" → (settings.transcribe_model, diarize)
    - anything ending in "-diarize" → (base_model, True)
    - explicit model name → (that_model, diarize)
    """
    if not requested:
        return settings.transcribe_model, diarize
    if requested.endswith("-diarize"):
        base = requested.removesuffix("-diarize")
        return base or settings.transcribe_model, True
    return requested, diarize


def get_stored_model(requested: str, diarize: bool = False) -> str:
    """Return the model name to store in records.

    Appends '-diarize' suffix when diarize is detected.
    E.g. ("", True) with TM_TRANSCRIBE_MODEL=gemini-2.0-flash → "gemini-2.0-flash-diarize"
         ("gpt-4o-transcribe-diarize", False) → "gpt-4o-transcribe-diarize"
    """
    model, should_diarize = resolve_model(requested, diarize)
    if should_diarize:
        return f"{model}-diarize"
    return model


def _get_duration(audio_path: Path) -> float:
    """Get audio duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", str(audio_path),
        ],
        capture_output=True, text=True, check=True,
    )
    try:
        info = json.loads(result.stdout)
        duration = float(info["format"]["duration"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "Could not read audio metadata — ffprobe returned unexpected output"
        ) from exc
    logger.info("ffprobe: %.1fs duration", duration)
    return duration


def prepare_chunks(audio_path: Path, duration_limit: int | None = None) -> list[Path]:
    """Split audio into chunks under the API 25MB limit using ffmpeg.
    Returns a list of file paths (single-element if no split needed).
    If duration_limit is set (seconds), truncate audio first."""
    duration = _get_duration(audio_path)

    # Truncate audio if duration_limit is set and shorter than actual duration
    if duration_limit and duration > duration_limit:
        trimmed = audio_path.parent / f"{audio_path.stem}_trimmed{audio_path.suffix}"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "quiet", "-i", str(audio_path),
             "-t", str(duration_limit), "-c", "copy", str(trimmed)],
            check=True,
        )
        logger.info("Trimmed audio to %ds (was %.0fs)", duration_limit, duration)
        audio_path = trimmed
        duration = float(duration_limit)

    file_size_mb = audio_path.stat().st_size / (1024 * 1024)

    if file_size_mb <= settings.max_chunk_size_mb and duration <= MAX_CHUNK_DURATION_SECONDS:
        logger.info("No chunking needed (%.1f MB, %.0fs)", file_size_mb, duration)
        return [audio_path]

    bytes_per_second = audio_path.stat().st_size / duration
    max_chunk_bytes = settings.max_chunk_size_mb * 1024 * 1024
    chunk_duration = min(max_chunk_bytes / bytes_per_second, MAX_CHUNK_DURATION_SECONDS)

    chunks = []
    offset = 0.0
    while offset < duration:
        chunk_path = audio_path.parent / f"{audio_path.stem}_chunk{len(chunks)}.{settings.audio_format}"
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "quiet",
                "-ss", str(offset),
                "-t", str(chunk_duration),
                "-i", str(audio_path),
                "-c", "copy",
                str(chunk_path),
            ],
            check=True,
        )
        chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)
        if chunk_size_mb > MAX_UPLOAD_SIZE_MB:
            raise RuntimeError(
                f"Chunk {len(chunks)} is {chunk_size_mb:.1f} MB, exceeds {MAX_UPLOAD_SIZE_MB} MB limit"
            )
        chunks.append(chunk_path)
        offset += chunk_duration

    logger.info("Split into %d chunks", len(chunks))
    return chunks


async def transcribe_chunk(chunk_path: Path, model: str = "", diarize: bool = False) -> str:
    """Send a single audio chunk to the transcription API and return the text."""
    actual_model = model or settings.transcribe_model

    chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)
    logger.info("Transcribing %s (%.1f MB) with %s (diarize=%s)", chunk_path.name, chunk_size_mb, actual_model, diarize)

    if is_gemini_model(actual_model):
        return await _transcribe_gemini(chunk_path, actual_model, diarize)

    if diarize:
        return await _transcribe_diarize(chunk_path, f"{actual_model}-diarize")
    return await _transcribe_base(chunk_path, actual_model)


async def _transcribe_base(chunk_path: Path, model: str) -> str:
    """Transcribe with the base model — returns plain text."""
    client = get_client(model)
    with open(chunk_path, "rb") as f:
        response = await client.audio.transcriptions.create(
            model=model,
            file=f,
            response_format="text",
        )
    text = response.strip()
    logger.info("Chunk complete (%d words)", len(text.split()))
    return text


async def _transcribe_diarize(chunk_path: Path, model: str) -> str:
    """Transcribe with diarization — returns 'Speaker: text' lines."""
    client = get_client(model)
    with open(chunk_path, "rb") as f:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Unexpected audio response format")
            response = await client.audio.transcriptions.create(
                model=model,
                file=f,
                response_format="diarized_json",
                chunking_strategy="auto",
            )
    lines = []
    for seg in response.segments:
        s = seg if isinstance(seg, dict) else seg.__dict__
        speaker = s.get("speaker") or "Unknown"
        seg_text = s.get("text", "").strip()
        if seg_text:
            lines.append(f"{speaker}: {seg_text}")
    text = "\n".join(lines) if lines else response.text or ""
    logger.info("Chunk complete (%d words, diarized)", len(text.split()))
    return text


async def _transcribe_gemini(chunk_path: Path, model: str, diarize: bool) -> str:
    """Transcribe audio using Gemini via the OpenAI-compatible chat endpoint."""
    client = get_client(model)

    audio_data = base64.b64encode(chunk_path.read_bytes()).decode("utf-8")

    if diarize:
        prompt = (
            "Transcribe this audio. Identify different speakers and "
            "format each line as 'A: text', 'B: text', etc. where each letter is a different speaker. "
            "Do not use any other formatting — no markdown, no bold, no headers."
        )
    else:
        prompt = (
            "Transcribe this audio verbatim as a single continuous block of plain text. "
            "Do not add speaker labels, headings, markdown formatting, or line breaks between sentences."
        )

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio_data,
                            "format": "mp3",
                        },
                    },
                ],
            },
        ],
        temperature=0.0,
    )
    text = response.choices[0].message.content.strip()
    logger.info("Gemini chunk complete (%d words, diarize=%s)", len(text.split()), diarize)
    return text


def cleanup_temp_files(audio_path: Path) -> None:
    """Remove the downloaded audio and any chunk files."""
    stem = audio_path.stem
    parent = audio_path.parent
    for f in parent.glob(f"{stem}*"):
        f.unlink(missing_ok=True)
