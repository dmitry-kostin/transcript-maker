from pathlib import Path

import pytest
from app.config import settings
from app.services.downloader import DownloadError, Downloader


class TestDownloader:
    @pytest.mark.asyncio
    async def test_returns_path_duration_title(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
        monkeypatch.setattr(settings, "audio_format", "mp3")

        fake_info = {"id": "dQw4w9WgXcQ", "duration": 213, "title": "Never Gonna Give You Up"}

        def fake_download(self, url, opts, accept=None):
            # Create the expected output file
            import re
            match = re.search(r"%(id)s_(\w+)\.%(ext)s", opts["outtmpl"])
            suffix = opts["outtmpl"].split("_")[-1].split(".")[0]
            # Parse the template to find the suffix
            template = opts["outtmpl"]
            output_path = template.replace("%(id)s", "dQw4w9WgXcQ").replace("%(ext)s", "mp3")
            Path(output_path).write_bytes(b"\x00" * 1000)
            return fake_info

        monkeypatch.setattr(Downloader, "_fetch", fake_download)

        path, duration, title = await Downloader(settings).download("https://youtube.com/watch?v=dQw4w9WgXcQ")
        assert path.exists()
        assert duration == 213
        assert title == "Never Gonna Give You Up"

    @pytest.mark.asyncio
    async def test_title_fallback_untitled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
        monkeypatch.setattr(settings, "audio_format", "mp3")

        fake_info = {"id": "abc12345678", "duration": 60}  # No title key

        def fake_download(self, url, opts, accept=None):
            template = opts["outtmpl"]
            output_path = template.replace("%(id)s", "abc12345678").replace("%(ext)s", "mp3")
            Path(output_path).write_bytes(b"\x00" * 1000)
            return fake_info

        monkeypatch.setattr(Downloader, "_fetch", fake_download)

        path, duration, title = await Downloader(settings).download("https://youtube.com/watch?v=abc12345678")
        assert title == "Untitled"

    @pytest.mark.asyncio
    async def test_raises_download_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path))

        def fake_download(self, url, opts, accept=None):
            raise DownloadError("Video unavailable")

        monkeypatch.setattr(Downloader, "_fetch", fake_download)

        with pytest.raises(DownloadError, match="Video unavailable"):
            await Downloader(settings).download("https://youtube.com/watch?v=bad")

    @pytest.mark.asyncio
    async def test_unique_suffix_in_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
        monkeypatch.setattr(settings, "audio_format", "mp3")

        paths_created = []

        def fake_download(self, url, opts, accept=None):
            template = opts["outtmpl"]
            output_path = template.replace("%(id)s", "same_vid").replace("%(ext)s", "mp3")
            Path(output_path).write_bytes(b"\x00" * 1000)
            paths_created.append(output_path)
            return {"id": "same_vid", "duration": 60, "title": "Same"}

        monkeypatch.setattr(Downloader, "_fetch", fake_download)

        path1, _, _ = await Downloader(settings).download("https://youtube.com/watch?v=same_vid")
        path2, _, _ = await Downloader(settings).download("https://youtube.com/watch?v=same_vid")
        assert path1 != path2  # Different UUID suffixes

    @pytest.mark.asyncio
    async def test_noplaylist_option_is_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
        monkeypatch.setattr(settings, "audio_format", "mp3")

        captured_opts = {}

        def fake_download(self, url, opts, accept=None):
            captured_opts.update(opts)
            template = opts["outtmpl"]
            output_path = template.replace("%(id)s", "dQw4w9WgXcQ").replace("%(ext)s", "mp3")
            Path(output_path).write_bytes(b"\x00" * 1000)
            return {"id": "dQw4w9WgXcQ", "duration": 60, "title": "Test"}

        monkeypatch.setattr(Downloader, "_fetch", fake_download)

        await Downloader(settings).download("https://youtube.com/watch?v=dQw4w9WgXcQ")
        assert captured_opts.get("noplaylist") is True


class TestDurationCheckedBeforeDownload:
    """``accept`` sees the duration from the metadata, before any audio is fetched."""

    @pytest.fixture
    def calls(self, monkeypatch) -> list:
        """yt-dlp replaced by a fake that records what was asked of it."""
        calls = []

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *exc): return False

            def extract_info(self, url, download):
                calls.append(("extract_info", download))
                return {"id": "abc", "duration": 7200, "title": "T"}

            def process_ie_result(self, info, download):
                calls.append(("process_ie_result", download))
                return info

        monkeypatch.setattr("app.services.downloader.yt_dlp.YoutubeDL", FakeYDL)
        return calls

    def test_metadata_first_then_download(self, calls):
        seen = []
        info = Downloader(settings)._fetch("url", {}, seen.append)
        assert seen == [7200.0]
        assert calls == [("extract_info", False), ("process_ie_result", True)]
        assert info["id"] == "abc"

    def test_refusal_stops_before_anything_is_fetched(self, calls):
        with pytest.raises(ZeroDivisionError):
            Downloader(settings)._fetch("url", {}, lambda seconds: 1 / 0)
        assert calls == [("extract_info", False)]
