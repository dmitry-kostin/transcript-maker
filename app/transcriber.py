import json
import logging
import subprocess
import warnings
from pathlib import Path

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=settings.openai_api_key)

MAX_UPLOAD_SIZE_MB = 25.0

DEFAULT_MODEL = "gpt-4o-transcribe"
SUPPORTED_MODELS = {"gpt-4o-transcribe", "gpt-4o-transcribe-diarize"}


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


def prepare_chunks(audio_path: Path) -> list[Path]:
    """Split audio into chunks under the API 25MB limit using ffmpeg.
    Returns a list of file paths (single-element if no split needed)."""
    file_size_mb = audio_path.stat().st_size / (1024 * 1024)

    if file_size_mb <= settings.max_chunk_size_mb:
        logger.info("No chunking needed (%.1f MB)", file_size_mb)
        return [audio_path]

    duration = _get_duration(audio_path)
    bytes_per_second = audio_path.stat().st_size / duration
    max_chunk_bytes = settings.max_chunk_size_mb * 1024 * 1024
    chunk_duration = max_chunk_bytes / bytes_per_second

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


async def transcribe_chunk(chunk_path: Path, model: str | None = None) -> str:
    """Send a single audio chunk to the transcription API and return the text."""
    model = model or DEFAULT_MODEL
    if model not in SUPPORTED_MODELS:
        raise ValueError(f"Unsupported model: {model}")

    chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)
    logger.info("Transcribing %s (%.1f MB) with %s", chunk_path.name, chunk_size_mb, model)

    if model == "gpt-4o-transcribe-diarize":
        return await _transcribe_diarize(chunk_path, model)
    return await _transcribe_base(chunk_path, model)


async def _transcribe_base(chunk_path: Path, model: str) -> str:
    """Transcribe with the base model — returns plain text."""
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


def cleanup_temp_files(audio_path: Path) -> None:
    """Remove the downloaded audio and any chunk files."""
    stem = audio_path.stem
    parent = audio_path.parent
    for f in parent.glob(f"{stem}*"):
        f.unlink(missing_ok=True)
