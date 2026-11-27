"""A markdown file with YAML frontmatter — the on-disk format of records and summaries."""
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class MarkdownFile:
    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, meta: dict, body: str = "") -> None:
        """Frontmatter keeps the order of ``meta``."""
        frontmatter = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip("\n")
        parts = [f"---\n{frontmatter}\n---"]
        if body:
            parts.append("")
            parts.append(body)
        self._path.write_text("\n".join(parts) + "\n", encoding="utf-8")

    def read(self) -> tuple[dict, str] | None:
        """(meta, body), every meta value a string. None if the file cannot be read."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Could not read %s", self._path)
            return None
        if not text.startswith("---"):
            return {}, ""
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, ""
        return self._meta(parts[1]), parts[2].strip()

    def _meta(self, frontmatter: str) -> dict:
        try:
            loaded = yaml.safe_load(frontmatter)
        except yaml.YAMLError:
            logger.warning("Invalid YAML frontmatter in %s", self._path)
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {key: str(value) if value is not None else "" for key, value in loaded.items()}
