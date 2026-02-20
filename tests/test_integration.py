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
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from json_repair import repair_json

from app.clients import get_client
from app.config import PROJECT_ROOT, settings
from app.downloader import download_audio
from app.summarizer import summarize_text
from app.transcriber import prepare_chunks, transcribe_chunk, cleanup_temp_files

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

VALIDATE_DIARIZED = (
    "Evaluate whether this is a diarized transcript with multiple lines in the format "
    "'Speaker N: text' (or similar speaker labels). Most lines should follow this pattern."
)

VALIDATE_SUMMARY = (
    "The text above the === separator is a summary; the text below is the source transcript. "
    "Evaluate whether the summary accurately and coherently captures the key points from the transcript."
)


logger = logging.getLogger(__name__)

# Pattern matching speaker-labelled lines: "Speaker 1:", "A:", "B:", etc.
_SPEAKER_LINE_RE = re.compile(r"^[A-Z][\w ]*:", re.MULTILINE)

# --- Audio caching ---

_CACHE_DIR = PROJECT_ROOT / "tmp" / "test_cache"
_CACHE_AUDIO = _CACHE_DIR / "audio.mp3"
_CACHE_META = _CACHE_DIR / "meta.json"

# In-memory cache for same-process reuse
_audio_cache: dict | None = None


async def _get_test_audio(tmp_path: Path) -> tuple[Path, float, str]:
    """Download test audio once and cache; copy to tmp_path for test isolation."""
    global _audio_cache

    # 1. In-memory cache hit
    if _audio_cache and _CACHE_AUDIO.exists():
        dst = tmp_path / _CACHE_AUDIO.name
        shutil.copy2(_CACHE_AUDIO, dst)
        return dst, _audio_cache["duration"], _audio_cache["title"]

    # 2. Disk cache hit
    if _CACHE_AUDIO.exists() and _CACHE_META.exists():
        meta = json.loads(_CACHE_META.read_text())
        _audio_cache = meta
        dst = tmp_path / _CACHE_AUDIO.name
        shutil.copy2(_CACHE_AUDIO, dst)
        return dst, meta["duration"], meta["title"]

    # 3. Cache miss — download and populate cache
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path, duration, title = await download_audio(TEST_VIDEO_URL)
    try:
        shutil.copy2(path, _CACHE_AUDIO)
        meta = {"duration": duration, "title": title}
        _CACHE_META.write_text(json.dumps(meta))
        _audio_cache = meta
    finally:
        cleanup_temp_files(path)

    dst = tmp_path / _CACHE_AUDIO.name
    shutil.copy2(_CACHE_AUDIO, dst)
    return dst, duration, title


# --- Debug report ---

_DEBUG_DIR = PROJECT_ROOT / "tests" / "debug"
_debug_report_path: Path | None = None


def _init_debug_report() -> Path:
    """Create debug dir and report file with a header."""
    global _debug_report_path
    if _debug_report_path and _debug_report_path.exists():
        return _debug_report_path

    _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _debug_report_path = _DEBUG_DIR / f"integration_{ts}.md"
    header = (
        f"# Integration Test Run — {ts}\n\n"
        f"- **Video:** {TEST_VIDEO_URL}\n"
        f"- **Duration limit:** {DURATION_LIMIT}s\n"
        f"- **OpenAI key:** {'yes' if has_openai_key else 'no'}\n"
        f"- **Gemini key:** {'yes' if has_gemini_key else 'no'}\n"
        f"- **Judge model:** {GEMINI_JUDGE}\n"
        f"- **Confidence threshold:** {CONFIDENCE_THRESHOLD}%\n\n"
        f"---\n\n"
    )
    _debug_report_path.write_text(header)
    return _debug_report_path


def _append_debug(section: str) -> None:
    """Append a markdown section to the debug report."""
    report = _init_debug_report()
    with report.open("a") as f:
        f.write(section + "\n\n")


# --- Helpers ---

def _has_speaker_labels(text: str) -> bool:
    """Check if text contains speaker-labelled lines (Speaker N:, A:, B:, etc.)."""
    return len(_SPEAKER_LINE_RE.findall(text)) >= 2


async def _validate_with_llm(text: str, prompt: str) -> dict:
    """Use Gemini as an LLM judge to validate transcription/summary quality.

    Returns dict with keys: confidence (int), justification (str), passed (bool).
    """
    client = get_client(GEMINI_JUDGE)
    response = await client.chat.completions.create(
        model=GEMINI_JUDGE,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a strict output validator. Respond ONLY with a JSON object: "
                    '{"confidence": <0-100>, "justification": "<brief explanation>"}. '
                    "Do not include any other text."
                ),
            },
            {"role": "user", "content": f"{prompt}\n\n---\n\n{text}"},
        ],
        temperature=0.0,
    )
    raw = response.choices[0].message.content.strip()

    result = repair_json(raw, return_objects=True)
    if isinstance(result, dict) and "confidence" in result:
        confidence = int(result.get("confidence", 0))
        justification = result.get("justification", "")
    else:
        confidence = 0
        justification = f"JSON parse failed, raw: {raw[:200]}"

    passed = confidence >= CONFIDENCE_THRESHOLD
    logger.info(
        "LLM judge (%s): %d%% confidence | %s | prompt: %s...",
        GEMINI_JUDGE, confidence, justification[:80], prompt[:60],
    )
    return {"confidence": confidence, "justification": justification, "passed": passed}


def _word_count(text: str) -> int:
    return len(text.split())


def _debug_transcription(
    test_name: str, model: str, diarize: bool, text: str,
    judge_prompt: str | None = None, judge_result: dict | None = None,
) -> None:
    """Append a transcription debug section."""
    wc = _word_count(text)
    lines = [
        f"## {test_name}",
        f"**Model:** {model} | **Diarize:** {'yes' if diarize else 'no'} "
        f"| **Duration limit:** {DURATION_LIMIT}s | **Words:** {wc}",
        "",
        "### Transcript",
        f"<details><summary>Full text ({wc} words)</summary>",
        "",
        text,
        "",
        "</details>",
    ]
    if judge_result and judge_prompt:
        lines += [
            "",
            f"### Judge: {judge_prompt.split('?')[0].split('.')[0][:50]}",
            f"- **Confidence:** {judge_result['confidence']}%",
            f"- **Justification:** {judge_result['justification']}",
        ]
    _append_debug("\n".join(lines))


def _debug_summary(
    test_name: str, model: str, transcript: str, summary: str,
    judge_result: dict | None = None,
) -> None:
    """Append a summarization debug section."""
    twc = _word_count(transcript)
    swc = _word_count(summary)
    lines = [
        f"## {test_name}",
        f"**Model:** {model} | **Duration limit:** {DURATION_LIMIT}s",
        "",
        "### Transcript",
        f"<details><summary>Full text ({twc} words)</summary>",
        "",
        transcript,
        "",
        "</details>",
        "",
        "### Summary",
        f"<details><summary>Full text ({swc} words)</summary>",
        "",
        summary,
        "",
        "</details>",
    ]
    if judge_result:
        lines += [
            "",
            "### Judge: VALIDATE_SUMMARY",
            f"- **Confidence:** {judge_result['confidence']}%",
            f"- **Justification:** {judge_result['justification']}",
        ]
    _append_debug("\n".join(lines))


@pytest.mark.integration
@skip_no_openai
class TestIntegration:
    @pytest.mark.asyncio
    async def test_download_returns_valid_audio(self, tmp_path, monkeypatch):
        import app.downloader as mod
        monkeypatch.setattr(mod.settings, "temp_dir", str(tmp_path))
        path, duration, title = await download_audio(TEST_VIDEO_URL)
        try:
            assert path.exists()
            assert path.stat().st_size > 0
            assert duration > 0
            assert title  # Non-empty
        finally:
            cleanup_temp_files(path)

    @pytest.mark.asyncio
    async def test_full_pipeline_with_history(self, tmp_path, monkeypatch):
        """Single Whisper call covering: transcription, chunking, and history record creation."""
        import app.history as history_mod
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)

        path, duration, title = await _get_test_audio(tmp_path)
        try:
            # Chunking
            chunks = prepare_chunks(path, duration_limit=DURATION_LIMIT)
            assert len(chunks) >= 1

            # Transcription
            texts = []
            for chunk in chunks:
                text = await transcribe_chunk(chunk)
                assert len(text) > 0
                assert isinstance(text, str)
                texts.append(text)
            full = " ".join(texts)
            assert len(full) > 10

            # History record
            rid = history_mod.create_record(title, TEST_VIDEO_URL, duration)
            history_mod.complete_record(rid, full)

            records = history_mod.get_history()
            assert len(records) == 1
            assert records[0]["status"] == "done"

            md_path = history_mod.get_result_path(rid)
            content = md_path.read_text()
            assert full in content
        finally:
            cleanup_temp_files(path)

    @pytest.mark.asyncio
    async def test_openai_transcription(self, tmp_path):
        """Download test video and transcribe with OpenAI (plain) + LLM quality check."""
        path, duration, title = await _get_test_audio(tmp_path)
        try:
            chunks = prepare_chunks(path, duration_limit=DURATION_LIMIT)
            text = await transcribe_chunk(chunks[0])
            assert len(text) > 0
            assert isinstance(text, str)

            judge_result = None
            if has_gemini_key:
                judge_result = await _validate_with_llm(text, VALIDATE_TRANSCRIPT)
                assert judge_result["passed"], judge_result["justification"]
            else:
                logger.warning("LLM judge skipped (no Gemini key) — no quality validation")

            _debug_transcription(
                "test_openai_transcription", settings.transcribe_model,
                diarize=False, text=text,
                judge_prompt=VALIDATE_TRANSCRIPT, judge_result=judge_result,
            )
        finally:
            cleanup_temp_files(path)

    @pytest.mark.asyncio
    async def test_openai_diarize_transcription(self, tmp_path):
        """Download test video and transcribe with diarization — expect 'Speaker' lines."""
        path, duration, title = await _get_test_audio(tmp_path)
        try:
            chunks = prepare_chunks(path, duration_limit=DURATION_LIMIT)
            text = await transcribe_chunk(chunks[0], diarize=True)
            assert len(text) > 0
            assert _has_speaker_labels(text), "Expected speaker-labelled lines (Speaker N: / A: / B: etc.)"

            judge_result = None
            if has_gemini_key:
                judge_result = await _validate_with_llm(text, VALIDATE_DIARIZED)
                assert judge_result["passed"], judge_result["justification"]
            else:
                logger.warning("LLM judge skipped (no Gemini key) — no quality validation")

            _debug_transcription(
                "test_openai_diarize_transcription", settings.transcribe_model,
                diarize=True, text=text,
                judge_prompt=VALIDATE_DIARIZED, judge_result=judge_result,
            )
        finally:
            cleanup_temp_files(path)

    @pytest.mark.asyncio
    async def test_openai_summarization(self, tmp_path):
        """Download, transcribe, then summarize with the default OpenAI model."""
        path, duration, title = await _get_test_audio(tmp_path)
        try:
            chunks = prepare_chunks(path, duration_limit=DURATION_LIMIT)
            transcript = await transcribe_chunk(chunks[0])
            assert len(transcript) > 0

            summary = await summarize_text(transcript)
            assert len(summary) > 0
            assert isinstance(summary, str)

            judge_result = None
            if has_gemini_key:
                judge_input = f"{summary}\n\n===\n\n{transcript}"
                judge_result = await _validate_with_llm(judge_input, VALIDATE_SUMMARY)
                assert judge_result["passed"], judge_result["justification"]
            else:
                logger.warning("LLM judge skipped (no Gemini key) — no quality validation")

            _debug_summary(
                "test_openai_summarization", settings.summarize_model,
                transcript=transcript, summary=summary, judge_result=judge_result,
            )
        finally:
            cleanup_temp_files(path)


@pytest.mark.integration
@skip_no_gemini
class TestGeminiIntegration:
    @pytest.mark.asyncio
    async def test_gemini_transcription(self, tmp_path):
        """Download test video and transcribe one chunk with Gemini."""
        path, duration, title = await _get_test_audio(tmp_path)
        try:
            chunks = prepare_chunks(path, duration_limit=DURATION_LIMIT)
            text = await transcribe_chunk(
                chunks[0], model=settings.gemini_transcribe_model,
            )
            assert len(text) > 0
            assert isinstance(text, str)
            judge_result = await _validate_with_llm(text, VALIDATE_TRANSCRIPT)
            assert judge_result["passed"], judge_result["justification"]

            _debug_transcription(
                "test_gemini_transcription", settings.gemini_transcribe_model,
                diarize=False, text=text,
                judge_prompt=VALIDATE_TRANSCRIPT, judge_result=judge_result,
            )
        finally:
            cleanup_temp_files(path)

    @pytest.mark.asyncio
    async def test_gemini_diarize_transcription(self, tmp_path):
        """Download test video and transcribe with Gemini diarization."""
        path, duration, title = await _get_test_audio(tmp_path)
        try:
            chunks = prepare_chunks(path, duration_limit=DURATION_LIMIT)
            text = await transcribe_chunk(
                chunks[0], model=settings.gemini_transcribe_model, diarize=True,
            )
            assert len(text) > 0
            assert _has_speaker_labels(text), "Expected speaker-labelled lines (Speaker N: / A: / B: etc.)"
            judge_result = await _validate_with_llm(text, VALIDATE_DIARIZED)
            assert judge_result["passed"], judge_result["justification"]

            _debug_transcription(
                "test_gemini_diarize_transcription", settings.gemini_transcribe_model,
                diarize=True, text=text,
                judge_prompt=VALIDATE_DIARIZED, judge_result=judge_result,
            )
        finally:
            cleanup_temp_files(path)

    @pytest.mark.asyncio
    async def test_gemini_summarization(self, tmp_path):
        """Download, transcribe, then summarize with the Gemini model."""
        path, duration, title = await _get_test_audio(tmp_path)
        try:
            chunks = prepare_chunks(path, duration_limit=DURATION_LIMIT)
            transcript = await transcribe_chunk(
                chunks[0], model=settings.gemini_transcribe_model,
            )
            assert len(transcript) > 0

            summary = await summarize_text(
                transcript, model=settings.gemini_summarize_model,
            )
            assert len(summary) > 0
            assert isinstance(summary, str)

            judge_input = f"{summary}\n\n===\n\n{transcript}"
            judge_result = await _validate_with_llm(judge_input, VALIDATE_SUMMARY)
            assert judge_result["passed"], judge_result["justification"]

            _debug_summary(
                "test_gemini_summarization", settings.gemini_summarize_model,
                transcript=transcript, summary=summary, judge_result=judge_result,
            )
        finally:
            cleanup_temp_files(path)
