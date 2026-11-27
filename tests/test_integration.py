"""End-to-end integration tests using real YouTube download + OpenAI/Gemini APIs.

These tests require:
- Internet access
- A valid TM_OPENAI_API_KEY in .env (for OpenAI tests)
- A valid GOOGLE_API_KEY env var (for Gemini tests)
- ffmpeg installed

Run with: poetry run pytest -m integration -v
"""

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from json_repair import repair_json

from app.config import PROJECT_ROOT, settings
from app.domain.model_policy import ModelPolicy
from app.services.chunker import AudioChunker
from app.services.clients import ClientFactory
from app.services.downloader import Downloader
from app.services.summarizer import Summarizer
from app.services.temp_files import TempDirectory
from app.services.transcriber import Transcriber

# The real services, wired as the app wires them
_POLICY = ModelPolicy()
_CLIENTS = ClientFactory(settings, _POLICY)
_DOWNLOADER = Downloader(settings)
_CHUNKER = AudioChunker(settings)
_TRANSCRIBER = Transcriber(_CLIENTS, _POLICY)
_SUMMARIZER = Summarizer(_CLIENTS, _POLICY)
_TEMP = TempDirectory(settings)

# Test video (~5.5 min)
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=ocGJWc2F1Yk"

# Duration limit for most tests (seconds) — saves API tokens
DURATION_LIMIT = 120

# Skip markers based on API key availability
has_openai_key = bool(settings.openai_api_key)
has_gemini_key = bool(settings.google_api_key)
skip_no_openai = pytest.mark.skipif(not has_openai_key, reason="TM_OPENAI_API_KEY not set")
skip_no_gemini = pytest.mark.skipif(not has_gemini_key, reason="GOOGLE_API_KEY not set")

# LLM judge model and validation prompts
GEMINI_JUDGE = settings.gemini_summarize_model
CONFIDENCE_THRESHOLD = 90

VALIDATE_TRANSCRIPT = (
    "Evaluate whether this is a coherent speech transcription containing natural "
    "language sentences that a human speaker would say — not garbled, empty, or nonsensical."
)

VALIDATE_SUMMARY = (
    "The text above the === separator is a summary; the text below is the source transcript. "
    "Evaluate whether the summary accurately and coherently captures the key points from the transcript."
)


logger = logging.getLogger(__name__)


class CachedTestAudio:
    """The test video's audio, downloaded once and cached in memory and on disk."""

    def __init__(self, downloader: Downloader, temp: TempDirectory, cache_dir: Path) -> None:
        self._downloader = downloader
        self._temp = temp
        self._cache_dir = cache_dir
        self._audio = cache_dir / "audio.mp3"
        self._meta_file = cache_dir / "meta.json"
        self._meta: dict | None = None  # in-memory, for reuse within one process

    async def copy_into(self, tmp_path: Path) -> tuple[Path, float, str]:
        """A private copy of the audio in ``tmp_path``: (path, duration, title)."""
        meta = self._cached_meta() or await self._download()
        copy = tmp_path / self._audio.name
        shutil.copy2(self._audio, copy)
        return copy, meta["duration"], meta["title"]

    def _cached_meta(self) -> dict | None:
        if not self._audio.exists():
            return None
        if self._meta is None and self._meta_file.exists():
            self._meta = json.loads(self._meta_file.read_text())
        return self._meta

    async def _download(self) -> dict:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path, duration, title = await self._downloader.download(TEST_VIDEO_URL)
        try:
            shutil.copy2(path, self._audio)
            self._meta = {"duration": duration, "title": title}
            self._meta_file.write_text(json.dumps(self._meta))
        finally:
            self._temp.remove_run_files(path)
        return self._meta


class DebugReport:
    """One markdown file per test run with full outputs, word counts and judge scores."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._path: Path | None = None

    def transcription(
        self,
        test_name: str,
        model: str,
        text: str,
        judge_prompt: str | None = None,
        judge_result: dict | None = None,
    ) -> None:
        lines = [
            f"## {test_name}",
            f"**Model:** {model} | **Duration limit:** {DURATION_LIMIT}s | **Words:** {self._word_count(text)}",
            "",
            *self._collapsed("Transcript", text),
        ]
        if judge_result and judge_prompt:
            lines += self._judgement(judge_prompt.split("?")[0].split(".")[0][:50], judge_result)
        self._append("\n".join(lines))

    def summary(
        self,
        test_name: str,
        model: str,
        transcript: str,
        summary: str,
        judge_result: dict | None = None,
    ) -> None:
        lines = [
            f"## {test_name}",
            f"**Model:** {model} | **Duration limit:** {DURATION_LIMIT}s",
            "",
            *self._collapsed("Transcript", transcript),
            "",
            *self._collapsed("Summary", summary),
        ]
        if judge_result:
            lines += self._judgement("VALIDATE_SUMMARY", judge_result)
        self._append("\n".join(lines))

    def _collapsed(self, heading: str, text: str) -> list[str]:
        return [
            f"### {heading}",
            f"<details><summary>Full text ({self._word_count(text)} words)</summary>",
            "",
            text,
            "",
            "</details>",
        ]

    def _judgement(self, label: str, judge_result: dict) -> list[str]:
        return [
            "",
            f"### Judge: {label}",
            f"- **Confidence:** {judge_result['confidence']}%",
            f"- **Justification:** {judge_result['justification']}",
        ]

    def _word_count(self, text: str) -> int:
        return len(text.split())

    def _append(self, section: str) -> None:
        with self._file().open("a") as report:
            report.write(section + "\n\n")

    def _file(self) -> Path:
        """The report file, created with its header on first use."""
        if self._path and self._path.exists():
            return self._path
        self._directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        self._path = self._directory / f"integration_{timestamp}.md"
        self._path.write_text(
            f"# Integration Test Run — {timestamp}\n\n"
            f"- **Video:** {TEST_VIDEO_URL}\n"
            f"- **Duration limit:** {DURATION_LIMIT}s\n"
            f"- **OpenAI key:** {'yes' if has_openai_key else 'no'}\n"
            f"- **Gemini key:** {'yes' if has_gemini_key else 'no'}\n"
            f"- **Judge model:** {GEMINI_JUDGE}\n"
            f"- **Confidence threshold:** {CONFIDENCE_THRESHOLD}%\n\n"
            f"---\n\n"
        )
        return self._path


class LlmJudge:
    """Gemini as a judge of transcription / summary quality."""

    SYSTEM_MESSAGE = (
        "You are a strict output validator. Respond ONLY with a JSON object: "
        '{"confidence": <0-100>, "justification": "<brief explanation>"}. '
        "Do not include any other text."
    )

    def __init__(self, clients: ClientFactory, model: str) -> None:
        self._clients = clients
        self._model = model

    async def validate(self, text: str, prompt: str) -> dict:
        """Returns dict with keys: confidence (int), justification (str), passed (bool)."""
        response = await self._clients.client_for(self._model).chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self.SYSTEM_MESSAGE},
                {"role": "user", "content": f"{prompt}\n\n---\n\n{text}"},
            ],
            temperature=0.0,
        )
        confidence, justification = self._verdict(response.choices[0].message.content.strip())
        logger.info(
            "LLM judge (%s): %d%% confidence | %s | prompt: %s...",
            self._model,
            confidence,
            justification[:80],
            prompt[:60],
        )
        return {
            "confidence": confidence,
            "justification": justification,
            "passed": confidence >= CONFIDENCE_THRESHOLD,
        }

    def _verdict(self, raw: str) -> tuple[int, str]:
        result = repair_json(raw, return_objects=True)
        if isinstance(result, dict) and "confidence" in result:
            return int(result.get("confidence", 0)), result.get("justification", "")
        return 0, f"JSON parse failed, raw: {raw[:200]}"


_AUDIO = CachedTestAudio(_DOWNLOADER, _TEMP, PROJECT_ROOT / "tmp" / "test_cache")
_REPORT = DebugReport(PROJECT_ROOT / "tests" / "debug")
_JUDGE = LlmJudge(_CLIENTS, GEMINI_JUDGE)


@pytest.mark.integration
@skip_no_openai
class TestIntegration:
    @pytest.mark.asyncio
    async def test_download_returns_valid_audio(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
        path, duration, title = await _DOWNLOADER.download(TEST_VIDEO_URL)
        try:
            assert path.exists()
            assert path.stat().st_size > 0
            assert duration > 0
            assert title  # Non-empty
        finally:
            _TEMP.remove_run_files(path)

    @pytest.mark.asyncio
    async def test_full_pipeline_with_history(self, tmp_path, history):
        """Single Whisper call covering: transcription, chunking, and history record creation."""

        path, duration, title = await _AUDIO.copy_into(tmp_path)
        try:
            # Chunking
            chunks = _CHUNKER.prepare_chunks(path, duration_limit=DURATION_LIMIT)
            assert len(chunks) >= 1

            # Transcription
            texts = []
            for chunk in chunks:
                text = await _TRANSCRIBER.transcribe_chunk(chunk, model=settings.transcribe_model)
                assert len(text) > 0
                assert isinstance(text, str)
                texts.append(text)
            full = " ".join(texts)
            assert len(full) > 10

            # History record
            rid = history.records.create(title, TEST_VIDEO_URL, duration)
            history.records.complete(rid, full)

            records = history.records.list_all()
            assert len(records) == 1
            assert records[0]["status"] == "done"

            md_path = history.records.file_path(rid)
            content = md_path.read_text()
            assert full in content
        finally:
            _TEMP.remove_run_files(path)

    @pytest.mark.asyncio
    async def test_openai_transcription(self, tmp_path):
        """Download test video and transcribe with OpenAI (plain) + LLM quality check."""
        path, duration, title = await _AUDIO.copy_into(tmp_path)
        try:
            chunks = _CHUNKER.prepare_chunks(path, duration_limit=DURATION_LIMIT)
            text = await _TRANSCRIBER.transcribe_chunk(chunks[0], model=settings.transcribe_model)
            assert len(text) > 0
            assert isinstance(text, str)

            judge_result = None
            if has_gemini_key:
                judge_result = await _JUDGE.validate(text, VALIDATE_TRANSCRIPT)
                assert judge_result["passed"], judge_result["justification"]
            else:
                logger.warning("LLM judge skipped (no Gemini key) — no quality validation")

            _REPORT.transcription(
                "test_openai_transcription",
                settings.transcribe_model,
                text=text,
                judge_prompt=VALIDATE_TRANSCRIPT,
                judge_result=judge_result,
            )
        finally:
            _TEMP.remove_run_files(path)

    @pytest.mark.asyncio
    async def test_openai_summarization(self, tmp_path):
        """Download, transcribe, then summarize with the default OpenAI model."""
        path, duration, title = await _AUDIO.copy_into(tmp_path)
        try:
            chunks = _CHUNKER.prepare_chunks(path, duration_limit=DURATION_LIMIT)
            transcript = await _TRANSCRIBER.transcribe_chunk(chunks[0], model=settings.transcribe_model)
            assert len(transcript) > 0

            summary = await _SUMMARIZER.summarize(transcript, "", model=settings.summarize_model)
            assert len(summary) > 0
            assert isinstance(summary, str)

            judge_result = None
            if has_gemini_key:
                judge_input = f"{summary}\n\n===\n\n{transcript}"
                judge_result = await _JUDGE.validate(judge_input, VALIDATE_SUMMARY)
                assert judge_result["passed"], judge_result["justification"]
            else:
                logger.warning("LLM judge skipped (no Gemini key) — no quality validation")

            _REPORT.summary(
                "test_openai_summarization",
                settings.summarize_model,
                transcript=transcript,
                summary=summary,
                judge_result=judge_result,
            )
        finally:
            _TEMP.remove_run_files(path)


@pytest.mark.integration
@skip_no_gemini
class TestGeminiIntegration:
    @pytest.mark.asyncio
    async def test_gemini_transcription(self, tmp_path):
        """Download test video and transcribe one chunk with Gemini."""
        path, duration, title = await _AUDIO.copy_into(tmp_path)
        try:
            chunks = _CHUNKER.prepare_chunks(path, duration_limit=DURATION_LIMIT)
            text = await _TRANSCRIBER.transcribe_chunk(
                chunks[0],
                model=settings.gemini_transcribe_model,
            )
            assert len(text) > 0
            assert isinstance(text, str)
            judge_result = await _JUDGE.validate(text, VALIDATE_TRANSCRIPT)
            assert judge_result["passed"], judge_result["justification"]

            _REPORT.transcription(
                "test_gemini_transcription",
                settings.gemini_transcribe_model,
                text=text,
                judge_prompt=VALIDATE_TRANSCRIPT,
                judge_result=judge_result,
            )
        finally:
            _TEMP.remove_run_files(path)

    @pytest.mark.asyncio
    async def test_gemini_summarization(self, tmp_path):
        """Download, transcribe, then summarize with the Gemini model."""
        path, duration, title = await _AUDIO.copy_into(tmp_path)
        try:
            chunks = _CHUNKER.prepare_chunks(path, duration_limit=DURATION_LIMIT)
            transcript = await _TRANSCRIBER.transcribe_chunk(
                chunks[0],
                model=settings.gemini_transcribe_model,
            )
            assert len(transcript) > 0

            summary = await _SUMMARIZER.summarize(
                transcript,
                "",
                model=settings.gemini_summarize_model,
            )
            assert len(summary) > 0
            assert isinstance(summary, str)

            judge_input = f"{summary}\n\n===\n\n{transcript}"
            judge_result = await _JUDGE.validate(judge_input, VALIDATE_SUMMARY)
            assert judge_result["passed"], judge_result["justification"]

            _REPORT.summary(
                "test_gemini_summarization",
                settings.gemini_summarize_model,
                transcript=transcript,
                summary=summary,
                judge_result=judge_result,
            )
        finally:
            _TEMP.remove_run_files(path)
