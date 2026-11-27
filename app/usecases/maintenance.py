"""Housekeeping: fail records nothing is working on any more, clear leftover temp files."""
import logging

from app.services.transcriber import purge_temp_dir
from app.storage.history import fail_stale_records, has_in_progress_records

logger = logging.getLogger(__name__)

# No chunk finished and no status change for this long → the run is considered dead
STALE_AFTER_SECONDS = 10 * 60

# A temp file written this recently may be a download still in flight
TEMP_FILE_GRACE_SECONDS = 60


def fail_records_left_by_previous_run() -> int:
    """At startup nothing can be running, so every in_progress record is a leftover."""
    return fail_stale_records("Server restarted during transcription")


def clean_up() -> dict:
    """Fail stale records and delete leftover temp files. Safe to call while a transcription runs.

    Temp files are left alone while any record is still in progress — its chunks
    live in tmp/ and are older than the grace period long before they are transcribed.
    """
    cleaned_records = fail_stale_records("Cleaned up stale record", older_than_seconds=STALE_AFTER_SECONDS)
    if has_in_progress_records():
        deleted_files = 0
        logger.info("Cleanup: a transcription is running, temp files left alone")
    else:
        deleted_files = purge_temp_dir(older_than_seconds=TEMP_FILE_GRACE_SECONDS)
    logger.info("Cleanup: deleted %d temp files, cleaned %d stale records", deleted_files, cleaned_records)
    return {"deleted_files": deleted_files, "cleaned_records": cleaned_records}
