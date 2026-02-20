import hashlib
import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.config import settings

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(settings.results_dir)


def _slugify(text: str, max_len: int = 50) -> str:
    """Convert text to a filesystem-safe slug."""
    s = text.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:max_len] or "untitled"


def _write_md(path: Path, meta: dict, body: str = "") -> None:
    """Write a markdown file with YAML frontmatter."""
    frontmatter = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip("\n")
    parts = [f"---\n{frontmatter}\n---"]
    if body:
        parts.append("")
        parts.append(body)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _parse_md(path: Path) -> dict | None:
    """Parse a markdown file with YAML frontmatter. Returns dict with meta + body + id."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Could not read %s", path)
        return None

    # Extract record_id from filename: *_{8hex}.md
    match = re.search(r"_([0-9a-f]{8})\.md$", path.name)
    if not match:
        return None
    record_id = match.group(1)

    meta = {}
    body = ""

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                loaded = yaml.safe_load(parts[1])
                if isinstance(loaded, dict):
                    meta = {k: str(v) if v is not None else "" for k, v in loaded.items()}
            except yaml.YAMLError:
                logger.warning("Invalid YAML frontmatter in %s", path)
            body = parts[2].strip()

    return {
        "id": record_id,
        "title": meta.get("title", "Untitled"),
        "url": meta.get("url", ""),
        "status": meta.get("status", "unknown"),
        "duration": int(meta.get("duration", 0) or 0),
        "duration_limit": int(meta.get("duration_limit", 0) or 0),
        "model": meta.get("model", ""),
        "words": int(meta.get("words", 0) or 0) or (len(body.split()) if body else 0),
        "created_at": meta.get("created_at", ""),
        "error": meta.get("error", ""),
        "body": body,
        "path": str(path),
    }


def create_record(title: str, url: str, duration: float, model: str = "", duration_limit: int = 0) -> str:
    """Create a new result .md file with status: in_progress. Returns record_id."""
    RESULTS_DIR.mkdir(exist_ok=True)
    record_id = uuid.uuid4().hex[:8]
    slug = _slugify(title)
    filename = f"{slug}_{record_id}.md"
    path = RESULTS_DIR / filename

    meta = {
        "title": title,
        "url": url,
        "status": "in_progress",
        "duration": int(duration),
        "duration_limit": duration_limit,
        "model": model,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "error": "",
    }
    _write_md(path, meta)
    logger.info("Record created: %s (in_progress) %r", record_id, title)
    return record_id


def complete_record(record_id: str, text: str) -> bool:
    """Update a record to status: done and write the transcript body. Returns True if written."""
    path = _resolve_path(record_id)
    if not path:
        logger.warning("Cannot complete record %s: file not found", record_id)
        return False

    parsed = _parse_md(path)
    if not parsed:
        return False

    words = len(text.split()) if text else 0
    meta = {
        "title": parsed["title"],
        "url": parsed["url"],
        "status": "done",
        "duration": parsed["duration"],
        "duration_limit": parsed.get("duration_limit", 0),
        "model": parsed.get("model", ""),
        "words": words,
        "created_at": parsed["created_at"],
        "error": "",
    }
    _write_md(path, meta, text)
    logger.info("Record completed: %s", record_id)
    return True


def fail_record(record_id: str, error_msg: str) -> bool:
    """Update a record to status: error. Returns True if written."""
    path = _resolve_path(record_id)
    if not path:
        logger.warning("Cannot fail record %s: file not found", record_id)
        return False

    parsed = _parse_md(path)
    if not parsed:
        return False

    meta = {
        "title": parsed["title"],
        "url": parsed["url"],
        "status": "error",
        "duration": parsed["duration"],
        "duration_limit": parsed.get("duration_limit", 0),
        "model": parsed.get("model", ""),
        "created_at": parsed["created_at"],
        "error": error_msg,
    }
    _write_md(path, meta)
    logger.info("Record failed: %s", record_id)
    return True


def get_record(record_id: str) -> dict | None:
    """Return a single record by ID, or None if not found."""
    path = _resolve_path(record_id)
    if not path:
        return None
    parsed = _parse_md(path)
    if parsed:
        parsed["has_summary"] = _summary_path(record_id).exists()
    return parsed


def reset_record(record_id: str, model: str = "", duration_limit: int | None = None) -> bool:
    """Reset an existing record back to in_progress with a new model. Returns True if written."""
    path = _resolve_path(record_id)
    if not path:
        return False
    parsed = _parse_md(path)
    if not parsed:
        return False
    meta = {
        "title": parsed["title"],
        "url": parsed["url"],
        "status": "in_progress",
        "duration": parsed["duration"],
        "duration_limit": duration_limit if duration_limit is not None else parsed.get("duration_limit", 0),
        "model": model,
        "created_at": parsed["created_at"],
        "error": "",
    }
    _write_md(path, meta)
    return True


def get_history() -> list[dict]:
    """Return all records, newest first."""
    RESULTS_DIR.mkdir(exist_ok=True)
    records = []
    for path in RESULTS_DIR.glob("*.md"):
        parsed = _parse_md(path)
        if parsed:
            # Don't send the full body in the list — just metadata
            parsed.pop("body", None)
            parsed.pop("path", None)
            parsed["has_summary"] = _summary_path(parsed["id"]).exists()
            records.append(parsed)
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return records


def get_record_status(record_id: str) -> str | None:
    """Return the status of a record, or None if not found."""
    path = _resolve_path(record_id)
    if not path:
        return None
    parsed = _parse_md(path)
    if not parsed:
        return None
    return parsed["status"]


def get_result_path(record_id: str) -> Path | None:
    """Resolve a record ID to its absolute file path (with traversal guard)."""
    return _resolve_path(record_id)


def save_audio(record_id: str, audio_path: Path) -> Path | None:
    """Copy downloaded audio to results/ for reuse. Returns cached path or None."""
    path = _resolve_path(record_id)
    if not path:
        return None
    cached = RESULTS_DIR / f"{record_id}{audio_path.suffix}"
    try:
        shutil.copy2(audio_path, cached)
    except OSError:
        logger.warning("Could not cache audio for %s", record_id)
        return None
    return cached


def get_audio_path(record_id: str) -> Path | None:
    """Find cached audio file for a record. Returns path or None."""
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return None
    RESULTS_DIR.mkdir(exist_ok=True)
    for path in RESULTS_DIR.glob(f"{record_id}.*"):
        if path.suffix != ".md":
            return path
    return None


def find_cached_audio_by_url(url: str) -> tuple[Path, dict] | None:
    """Find cached audio from a previous record with the same URL.

    Returns (audio_path, record_dict) or None.
    """
    RESULTS_DIR.mkdir(exist_ok=True)
    for path in RESULTS_DIR.glob("*.md"):
        parsed = _parse_md(path)
        if not parsed or parsed["url"] != url:
            continue
        audio = get_audio_path(parsed["id"])
        if audio:
            return audio, parsed
    return None


def delete_record(record_id: str) -> bool:
    """Delete a record's .md file, cached audio, and summary. Returns True if deleted."""
    path = _resolve_path(record_id)
    if not path:
        return False
    audio = get_audio_path(record_id)
    delete_summary(record_id)
    delete_chunk_cache(record_id)
    path.unlink(missing_ok=True)
    if audio:
        audio.unlink(missing_ok=True)
    logger.info("Record deleted: %s", record_id)
    return True


def _summary_path(record_id: str) -> Path:
    """Return the path for a record's summary sidecar file."""
    return RESULTS_DIR / f"{record_id}_summary.md"


def save_summary(record_id: str, summary: str, prompt: str = "") -> bool:
    """Save a summary for a record. Returns True if written."""
    path = _resolve_path(record_id)
    if not path:
        return False
    RESULTS_DIR.mkdir(exist_ok=True)
    meta = {
        "prompt": prompt,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_md(_summary_path(record_id), meta, summary)
    logger.info("Summary saved for record %s", record_id)
    return True


def get_summary(record_id: str) -> dict | None:
    """Return summary for a record, or None if not found."""
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return None
    sp = _summary_path(record_id)
    if not sp.exists():
        return None
    try:
        text = sp.read_text(encoding="utf-8")
    except OSError:
        return None
    meta = {}
    body = ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                loaded = yaml.safe_load(parts[1])
                if isinstance(loaded, dict):
                    meta = {k: str(v) if v is not None else "" for k, v in loaded.items()}
            except yaml.YAMLError:
                pass
            body = parts[2].strip()
    return {
        "prompt": meta.get("prompt", ""),
        "summary": body,
        "created_at": meta.get("created_at", ""),
    }


def delete_summary(record_id: str) -> None:
    """Delete a summary sidecar file if it exists."""
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return
    _summary_path(record_id).unlink(missing_ok=True)


def _chunk_cache_path(record_id: str) -> Path:
    """Return the path for a record's chunk cache sidecar file."""
    return RESULTS_DIR / f"{record_id}_chunks.json"


def _chunk_cache_key(model: str, diarize: bool, total: int) -> str:
    """Hash of parameters that invalidate the chunk cache."""
    raw = f"{model}|{diarize}|{total}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_chunk_cache(record_id: str, model: str, diarize: bool, total: int) -> list[str]:
    """Load cached chunk transcriptions. Returns list of completed parts (may be shorter than total).
    Returns empty list if cache missing or invalidated (model/diarize/total mismatch)."""
    path = _chunk_cache_path(record_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read chunk cache for %s", record_id)
        return []
    if data.get("cache_key") != _chunk_cache_key(model, diarize, total):
        logger.info("Chunk cache invalidated for %s (parameter mismatch)", record_id)
        return []
    parts = data.get("parts", [])
    if not isinstance(parts, list):
        return []
    return parts


def save_chunk_cache(record_id: str, model: str, diarize: bool, total: int, parts: list[str]) -> None:
    """Persist current chunk transcription progress."""
    RESULTS_DIR.mkdir(exist_ok=True)
    data = {"cache_key": _chunk_cache_key(model, diarize, total), "parts": parts}
    _chunk_cache_path(record_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def delete_chunk_cache(record_id: str) -> None:
    """Remove chunk cache sidecar file."""
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return
    _chunk_cache_path(record_id).unlink(missing_ok=True)


def cleanup_stale_records() -> None:
    """Mark any in_progress records as failed (stale from prior crash)."""
    RESULTS_DIR.mkdir(exist_ok=True)
    for path in RESULTS_DIR.glob("*.md"):
        parsed = _parse_md(path)
        if parsed and parsed["status"] == "in_progress":
            fail_record(parsed["id"], "Server restarted during transcription")
            logger.warning("Cleaned up stale record: %s", parsed["id"])


def _resolve_path(record_id: str) -> Path | None:
    """Find the .md file for a record ID. Returns None if not found or traversal detected."""
    if not re.fullmatch(r"[0-9a-f]{8}", record_id):
        return None
    RESULTS_DIR.mkdir(exist_ok=True)
    matches = list(RESULTS_DIR.glob(f"*_{record_id}.md"))
    if len(matches) != 1:
        return None
    path = matches[0].resolve()
    if path.parent != RESULTS_DIR.resolve():
        return None
    return path
