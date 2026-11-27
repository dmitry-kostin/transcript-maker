"""ffmpeg / ffprobe: cut audio into pieces the transcription APIs accept."""

import json
import logging
import subprocess
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)

MAX_UPLOAD_SIZE_MB = 25.0
# Speech-to-text models silently truncate output on long chunks; 10 min keeps it well within token limits
MAX_CHUNK_DURATION_SECONDS = 600
BYTES_PER_MB = 1024 * 1024


class AudioChunker:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def prepare_chunks(self, audio_path: Path, duration_limit: int | None = None) -> list[Path]:
        """Split audio into chunks under the size and length limits. Blocking.

        Returns the audio itself when no split is needed. With ``duration_limit``
        (seconds) only that much of the start is kept.
        """
        duration = self.duration(audio_path)
        if duration_limit and duration > duration_limit:
            audio_path = self._trim(audio_path, duration_limit, duration)
            duration = float(duration_limit)

        size_mb = self._size_mb(audio_path)
        if size_mb <= self._settings.max_chunk_size_mb and duration <= MAX_CHUNK_DURATION_SECONDS:
            logger.info("No chunking needed (%.1f MB, %.0fs)", size_mb, duration)
            return [audio_path]
        return self._split(audio_path, duration)

    def duration(self, audio_path: Path) -> float:
        """Audio duration in seconds, via ffprobe."""
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(audio_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        try:
            duration = float(json.loads(result.stdout)["format"]["duration"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Could not read audio metadata — ffprobe returned unexpected output") from exc
        logger.info("ffprobe: %.1fs duration", duration)
        return duration

    def _trim(self, audio_path: Path, duration_limit: int, duration: float) -> Path:
        trimmed = audio_path.parent / f"{audio_path.stem}_trimmed{audio_path.suffix}"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "quiet",
                "-i",
                str(audio_path),
                "-t",
                str(duration_limit),
                "-c",
                "copy",
                str(trimmed),
            ],
            check=True,
        )
        logger.info("Trimmed audio to %ds (was %.0fs)", duration_limit, duration)
        return trimmed

    def _split(self, audio_path: Path, duration: float) -> list[Path]:
        chunk_duration = self._chunk_duration(audio_path, duration)
        chunks: list[Path] = []
        total_chunk_duration = 0.0
        offset = 0.0
        while offset < duration:
            chunk = self._cut(audio_path, len(chunks), offset, chunk_duration)
            actual_duration = self.duration(chunk)
            total_chunk_duration += actual_duration
            logger.info(
                "Chunk %d: offset=%.1fs, %.1f MB, %.1fs actual duration",
                len(chunks),
                offset,
                self._size_mb(chunk),
                actual_duration,
            )
            chunks.append(chunk)
            offset += chunk_duration
        logger.info(
            "Split into %d chunks (total chunk duration=%.1fs, original=%.1fs, diff=%.1fs)",
            len(chunks),
            total_chunk_duration,
            duration,
            total_chunk_duration - duration,
        )
        return chunks

    def _chunk_duration(self, audio_path: Path, duration: float) -> float:
        """As long as the size limit allows at this bitrate, capped by the length limit."""
        bytes_per_second = audio_path.stat().st_size / duration
        max_chunk_bytes = self._settings.max_chunk_size_mb * BYTES_PER_MB
        return min(max_chunk_bytes / bytes_per_second, MAX_CHUNK_DURATION_SECONDS)

    def _cut(self, audio_path: Path, index: int, offset: float, chunk_duration: float) -> Path:
        chunk = audio_path.parent / f"{audio_path.stem}_chunk{index}.{self._settings.audio_format}"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "quiet",
                "-ss",
                str(offset),
                "-t",
                str(chunk_duration),
                "-i",
                str(audio_path),
                "-c",
                "copy",
                str(chunk),
            ],
            check=True,
        )
        size_mb = self._size_mb(chunk)
        if size_mb > MAX_UPLOAD_SIZE_MB:
            raise RuntimeError(f"Chunk {index} is {size_mb:.1f} MB, exceeds {MAX_UPLOAD_SIZE_MB} MB limit")
        return chunk

    def _size_mb(self, path: Path) -> float:
        return path.stat().st_size / BYTES_PER_MB
