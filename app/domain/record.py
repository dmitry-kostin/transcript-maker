"""What a transcription record is: its identity, its statuses, and what may happen to it in each.

Pure rules — the record itself stays a plain dict that ``app.storage`` reads and writes.
"""

import re
import uuid

STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_ERROR = "error"


class RecordId:
    """Record IDs are 8 lowercase hex chars. Check ``is_valid`` before any file operation."""

    _FORMAT = re.compile(r"[0-9a-f]{8}")
    _IN_FILENAME = re.compile(r"_([0-9a-f]{8})\.md$")

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return bool(cls._FORMAT.fullmatch(value))

    @classmethod
    def new(cls) -> str:
        return uuid.uuid4().hex[:8]

    @classmethod
    def from_filename(cls, name: str) -> str | None:
        """The ID in a record filename (``*_{8hex}.md``); None for any other file."""
        match = cls._IN_FILENAME.search(name)
        return match.group(1) if match else None


class RecordLifecycle:
    """The rules of a record's life: in progress → done or error, and back in progress to be transcribed again."""

    @classmethod
    def may_enter(cls, current_status: str, new_status: str) -> bool:
        """A record that is in progress cannot be put in progress again.

        That is what keeps two concurrent re-transcriptions of one record apart.
        """
        return not (current_status == STATUS_IN_PROGRESS and new_status == STATUS_IN_PROGRESS)

    @classmethod
    def is_in_progress(cls, record: dict) -> bool:
        return record["status"] == STATUS_IN_PROGRESS

    @classmethod
    def can_be_summarized(cls, record: dict) -> bool:
        """Only a finished transcript can be summarized."""
        return record["status"] == STATUS_DONE

    @classmethod
    def belongs_to_mode(cls, record: dict, demo: bool) -> bool:
        """A record belongs to the mode that made it: only that mode may rewrite it or its summary."""
        return record["demo"] == demo
