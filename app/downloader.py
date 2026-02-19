import asyncio
import logging
import uuid
from pathlib import Path

import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    pass


async def download_audio(url: str) -> tuple[Path, float, str]:
    """Download audio from a YouTube URL. Returns (file_path, duration_seconds, title)."""
    output_dir = Path(settings.temp_dir)
    output_dir.mkdir(exist_ok=True)

    suffix = uuid.uuid4().hex[:8]
    output_template = str(output_dir / f"%(id)s_{suffix}.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": settings.audio_format,
                "preferredquality": "64",
            }
        ],
        "quiet": True,
        "no_warnings": True,
    }

    logger.info("Download started: %s", url)
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _download_sync, url, ydl_opts)

    video_id = info["id"]
    duration = info.get("duration", 0) or 0
    title = info.get("title", "Untitled")
    audio_path = output_dir / f"{video_id}_{suffix}.{settings.audio_format}"

    if not audio_path.exists():
        logger.error("Audio file not found at %s", audio_path)
        raise DownloadError(f"Audio file not found at {audio_path}")

    file_size_mb = audio_path.stat().st_size / (1024 * 1024)
    logger.info("Download complete: %.1f MB, %ds, %r", file_size_mb, duration, title)

    return audio_path, duration, title


def _download_sync(url: str, opts: dict) -> dict:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"Failed to download: {e}") from e
