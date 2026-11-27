import hashlib
import json
import logging
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.config import settings

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(settings.results_dir)

STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_ERROR = "error"

_RECORD_ID_RE = re.compile(r"[0-9a-f]{8}")


def is_valid_record_id(record_id: str) -> bool:
    """Record IDs are 8 lowercase hex chars. Check this before any file operation."""
    return bool(_RECORD_ID_RE.fullmatch(record_id))


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
        "status": STATUS_IN_PROGRESS,
        "duration": int(duration),
        "duration_limit": duration_limit,
        "model": model,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "error": "",
    }
    _write_md(path, meta)
    logger.info("Record created: %s (in_progress) %r", record_id, title)
    return record_id


def _transition(
    record_id: str,
    status: str,
    *,
    body: str | None = None,
    error: str = "",
    model: str | None = None,
    duration_limit: int | None = None,
) -> bool:
    """Move a record to a new status, rewriting its frontmatter. Returns True if written.

    The one place that rebuilds the full meta dict, so every status change keeps all
    fields in the canonical order. ``body`` / ``model`` / ``duration_limit`` keep their
    stored value unless given — so a re-transcription that fails or is interrupted
    leaves the previous transcript in the file; only a successful one replaces it.

    A record that is already in progress cannot be put in progress again: that is
    how two concurrent re-transcriptions of one record are kept apart.
    """
    path = _resolve_path(record_id)
    if not path:
        logger.warning("Cannot mark record %s as %s: file not found", record_id, status)
        return False
    parsed = _parse_md(path)
    if not parsed:
        return False
    if status == STATUS_IN_PROGRESS and parsed["status"] == STATUS_IN_PROGRESS:
        logger.warning("Record %s is already in progress", record_id)
        return False

    body = parsed["body"] if body is None else body
    meta = {
        "title": parsed["title"],
        "url": parsed["url"],
        "status": status,
        "duration": parsed["duration"],
        "duration_limit": parsed.get("duration_limit", 0) if duration_limit is None else duration_limit,
        "model": parsed.get("model", "") if model is None else model,
    }
    if status == STATUS_DONE:
        meta["words"] = len(body.split())
    meta["created_at"] = parsed["created_at"]
    meta["error"] = error
    _write_md(path, meta, body)
    logger.info("Record %s: %s", status, record_id)
    return True


def complete_record(record_id: str, text: str) -> bool:
    """Update a record to status: done and write the transcript body. Returns True if written."""
    return _transition(record_id, STATUS_DONE, body=text)


def fail_record(record_id: str, error_msg: str) -> bool:
    """Update a record to status: error. Returns True if written."""
    return _transition(record_id, STATUS_ERROR, error=error_msg)


def get_record(record_id: str) -> dict | None:
    """Return a single record by ID, or None if not found."""
    path = _resolve_path(record_id)
    if not path:
        return None
    parsed = _parse_md(path)
    if parsed:
        parsed["has_summary"] = _summary_path(record_id).exists()
    return parsed


def reset_record(record_id: str, model: str | None = None, duration_limit: int | None = None) -> bool:
    """Put a finished or failed record back in progress for a re-transcription.

    ``model`` / ``duration_limit`` replace the stored values when given. Returns False
    when the record is missing or something is already working on it.
    """
    return _transition(record_id, STATUS_IN_PROGRESS, model=model, duration_limit=duration_limit)


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
    if not is_valid_record_id(record_id):
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
    if not is_valid_record_id(record_id):
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
    if not is_valid_record_id(record_id):
        return
    _summary_path(record_id).unlink(missing_ok=True)


def _chunk_cache_path(record_id: str) -> Path:
    """Return the path for a record's chunk cache sidecar file."""
    return RESULTS_DIR / f"{record_id}_chunks.json"


def _chunk_cache_key(model: str, total: int) -> str:
    """Hash of parameters that invalidate the chunk cache."""
    raw = f"{model}|{total}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_chunk_cache(record_id: str, model: str, total: int) -> list[str]:
    """Load cached chunk transcriptions. Returns list of completed parts (may be shorter than total).
    Returns empty list if cache missing or invalidated (model/total mismatch)."""
    path = _chunk_cache_path(record_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read chunk cache for %s", record_id)
        return []
    if data.get("cache_key") != _chunk_cache_key(model, total):
        logger.info("Chunk cache invalidated for %s (parameter mismatch)", record_id)
        return []
    parts = data.get("parts", [])
    if not isinstance(parts, list):
        return []
    return parts


def save_chunk_cache(record_id: str, model: str, total: int, parts: list[str]) -> None:
    """Persist current chunk transcription progress."""
    RESULTS_DIR.mkdir(exist_ok=True)
    data = {"cache_key": _chunk_cache_key(model, total), "parts": parts}
    _chunk_cache_path(record_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def delete_chunk_cache(record_id: str) -> None:
    """Remove chunk cache sidecar file."""
    if not is_valid_record_id(record_id):
        return
    _chunk_cache_path(record_id).unlink(missing_ok=True)


def _last_activity(record_id: str, path: Path) -> float:
    """When a record was last worked on: its own mtime, or its chunk cache's if newer.

    The record file is only written at status changes; the chunk cache is rewritten
    after every chunk, so it is the heartbeat of a long multi-chunk transcription.
    """
    latest = path.stat().st_mtime
    try:
        latest = max(latest, _chunk_cache_path(record_id).stat().st_mtime)
    except OSError:
        pass
    return latest


def has_in_progress_records() -> bool:
    """True while any record is being transcribed."""
    RESULTS_DIR.mkdir(exist_ok=True)
    for path in RESULTS_DIR.glob("*.md"):
        parsed = _parse_md(path)
        if parsed and parsed["status"] == STATUS_IN_PROGRESS:
            return True
    return False


def fail_stale_records(reason: str, older_than_seconds: float = 0) -> int:
    """Fail in_progress records that nothing is working on any more. Returns how many.

    With ``older_than_seconds`` only records with no activity for that long are
    failed; with 0 every in_progress record is.
    """
    RESULTS_DIR.mkdir(exist_ok=True)
    now = time.time()
    failed = 0
    for path in RESULTS_DIR.glob("*.md"):
        parsed = _parse_md(path)
        if not parsed or parsed["status"] != STATUS_IN_PROGRESS:
            continue
        if older_than_seconds and now - _last_activity(parsed["id"], path) <= older_than_seconds:
            continue
        if fail_record(parsed["id"], reason):
            failed += 1
            logger.warning("Cleaned up stale record: %s", parsed["id"])
    return failed


def _resolve_path(record_id: str) -> Path | None:
    """Find the .md file for a record ID. Returns None if not found or traversal detected."""
    if not is_valid_record_id(record_id):
        return None
    RESULTS_DIR.mkdir(exist_ok=True)
    matches = list(RESULTS_DIR.glob(f"*_{record_id}.md"))
    if len(matches) != 1:
        return None
    path = matches[0].resolve()
    if path.parent != RESULTS_DIR.resolve():
        return None
    return path
