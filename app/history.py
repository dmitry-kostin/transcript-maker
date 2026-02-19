import logging
import re
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
        "created_at": meta.get("created_at", ""),
        "error": meta.get("error", ""),
        "body": body,
        "path": str(path),
    }


def create_record(title: str, url: str, duration: float) -> str:
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

    meta = {
        "title": parsed["title"],
        "url": parsed["url"],
        "status": "done",
        "duration": parsed["duration"],
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
        "created_at": parsed["created_at"],
        "error": error_msg,
    }
    _write_md(path, meta)
    logger.info("Record failed: %s", record_id)
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


def delete_record(record_id: str) -> bool:
    """Delete a record's .md file. Returns True if deleted."""
    path = _resolve_path(record_id)
    if not path:
        return False
    path.unlink(missing_ok=True)
    logger.info("Record deleted: %s", record_id)
    return True


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
