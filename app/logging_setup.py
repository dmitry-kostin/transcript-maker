"""Console + rotating-file logging for the app and uvicorn loggers."""
import logging
import logging.handlers
from pathlib import Path

from app.config import settings

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
LOG_MAX_BYTES = 5_000_000
LOG_BACKUP_COUNT = 3
LOGGER_NAMES = ("app", "uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging() -> Path | None:
    """Install the handlers. Returns the log file path, or None when file logging is off.

    Everything is mirrored to a rotating file so logs are readable while the server
    runs in another process (tail -f logs/app.log). TM_LOG_FILE="" disables that.
    Safe to call more than once — handlers are replaced, not stacked.
    """
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%H:%M:%S"))
    handlers: list[logging.Handler] = [console]

    log_file = Path(settings.log_file) if settings.log_file else None
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
        )
        # The file spans days, so it carries the date too
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
        handlers.append(file_handler)

    for name in LOGGER_NAMES:
        target = logging.getLogger(name)
        for old in target.handlers[:]:
            target.removeHandler(old)
            old.close()
        for handler in handlers:
            target.addHandler(handler)
        target.setLevel(logging.INFO)
        target.propagate = False
    return log_file
