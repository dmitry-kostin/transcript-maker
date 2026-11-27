"""yt-dlp: fetch a video's audio into tmp/."""
import asyncio
import logging
import uuid
from collections.abc import Callable
from pathlib import Path

import yt_dlp

from app.config import Settings

logger = logging.getLogger(__name__)

AUDIO_QUALITY_KBPS = "64"


class DownloadError(Exception):
    pass


class Downloader:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def download(self, url: str, accept: Callable[[float], None] | None = None) -> tuple[Path, float, str]:
        """Download audio from a YouTube URL. Returns (file_path, duration_seconds, title).

        ``accept`` is called with the video's duration in seconds once it is known and
        before any audio is fetched; whatever it raises aborts the download.
        """
        output_dir = Path(self._settings.temp_dir)
        output_dir.mkdir(exist_ok=True)
        suffix = uuid.uuid4().hex[:8]

        logger.info("Download started: %s", url)
        options = self._options(str(output_dir / f"%(id)s_{suffix}.%(ext)s"))
        info = await asyncio.get_running_loop().run_in_executor(None, self._fetch, url, options, accept)

        audio_path = output_dir / f"{info['id']}_{suffix}.{self._settings.audio_format}"
        if not audio_path.exists():
            logger.error("Audio file not found at %s", audio_path)
            raise DownloadError(f"Audio file not found at {audio_path}")

        duration = info.get("duration", 0) or 0
        title = info.get("title", "Untitled")
        file_size_mb = audio_path.stat().st_size / (1024 * 1024)
        logger.info("Download complete: %.1f MB, %ds, %r", file_size_mb, duration, title)
        return audio_path, duration, title

    def _options(self, output_template: str) -> dict:
        return {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": self._settings.audio_format,
                    "preferredquality": AUDIO_QUALITY_KBPS,
                }
            ],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }

    def _fetch(self, url: str, options: dict, accept: Callable[[float], None] | None = None) -> dict:
        """Blocking. Metadata first, so a video that will be refused is never downloaded."""
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                if accept is not None:
                    accept(float(info.get("duration") or 0))
                return ydl.process_ie_result(info, download=True)
        except yt_dlp.utils.DownloadError as e:
            raise DownloadError(f"Failed to download: {e}") from e
