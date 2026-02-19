import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from app.downloader import download_audio, DownloadError


class TestDownloader:
    @pytest.mark.asyncio
    async def test_returns_path_duration_title(self, tmp_path, monkeypatch):
        import app.downloader as mod
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path))
        monkeypatch.setattr(mod.settings, "audio_format", "mp3")

        fake_info = {"id": "dQw4w9WgXcQ", "duration": 213, "title": "Never Gonna Give You Up"}

        def fake_download(url, opts):
            # Create the expected output file
            import re
            match = re.search(r"%(id)s_(\w+)\.%(ext)s", opts["outtmpl"])
            suffix = opts["outtmpl"].split("_")[-1].split(".")[0]
            # Parse the template to find the suffix
            template = opts["outtmpl"]
            output_path = template.replace("%(id)s", "dQw4w9WgXcQ").replace("%(ext)s", "mp3")
            Path(output_path).write_bytes(b"\x00" * 1000)
            return fake_info

        monkeypatch.setattr(mod, "_download_sync", fake_download)

        path, duration, title = await download_audio("https://youtube.com/watch?v=dQw4w9WgXcQ")
        assert path.exists()
        assert duration == 213
        assert title == "Never Gonna Give You Up"

    @pytest.mark.asyncio
    async def test_title_fallback_untitled(self, tmp_path, monkeypatch):
        import app.downloader as mod
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path))
        monkeypatch.setattr(mod.settings, "audio_format", "mp3")

        fake_info = {"id": "abc12345678", "duration": 60}  # No title key

        def fake_download(url, opts):
            template = opts["outtmpl"]
            output_path = template.replace("%(id)s", "abc12345678").replace("%(ext)s", "mp3")
            Path(output_path).write_bytes(b"\x00" * 1000)
            return fake_info

        monkeypatch.setattr(mod, "_download_sync", fake_download)

        path, duration, title = await download_audio("https://youtube.com/watch?v=abc12345678")
        assert title == "Untitled"

    @pytest.mark.asyncio
    async def test_raises_download_error(self, tmp_path, monkeypatch):
        import app.downloader as mod
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path))

        def fake_download(url, opts):
            raise DownloadError("Video unavailable")

        monkeypatch.setattr(mod, "_download_sync", fake_download)

        with pytest.raises(DownloadError, match="Video unavailable"):
            await download_audio("https://youtube.com/watch?v=bad")

    @pytest.mark.asyncio
    async def test_unique_suffix_in_filename(self, tmp_path, monkeypatch):
        import app.downloader as mod
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path))
        monkeypatch.setattr(mod.settings, "audio_format", "mp3")

        paths_created = []

        def fake_download(url, opts):
            template = opts["outtmpl"]
            output_path = template.replace("%(id)s", "same_vid").replace("%(ext)s", "mp3")
            Path(output_path).write_bytes(b"\x00" * 1000)
            paths_created.append(output_path)
            return {"id": "same_vid", "duration": 60, "title": "Same"}

        monkeypatch.setattr(mod, "_download_sync", fake_download)

        path1, _, _ = await download_audio("https://youtube.com/watch?v=same_vid")
        path2, _, _ = await download_audio("https://youtube.com/watch?v=same_vid")
        assert path1 != path2  # Different UUID suffixes

    @pytest.mark.asyncio
    async def test_noplaylist_option_is_set(self, tmp_path, monkeypatch):
        import app.downloader as mod
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path))
        monkeypatch.setattr(mod.settings, "audio_format", "mp3")

        captured_opts = {}

        def fake_download(url, opts):
            captured_opts.update(opts)
            template = opts["outtmpl"]
            output_path = template.replace("%(id)s", "dQw4w9WgXcQ").replace("%(ext)s", "mp3")
            Path(output_path).write_bytes(b"\x00" * 1000)
            return {"id": "dQw4w9WgXcQ", "duration": 60, "title": "Test"}

        monkeypatch.setattr(mod, "_download_sync", fake_download)

        await download_audio("https://youtube.com/watch?v=dQw4w9WgXcQ")
        assert captured_opts.get("noplaylist") is True
