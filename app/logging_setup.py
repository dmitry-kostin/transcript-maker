"""Console + rotating-file logging for the app and uvicorn loggers."""
import logging
import logging.handlers
from pathlib import Path

from app.config import Settings

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
LOG_MAX_BYTES = 5_000_000
LOG_BACKUP_COUNT = 3
LOGGER_NAMES = ("app", "uvicorn", "uvicorn.error", "uvicorn.access")


class LoggingSetup:
    """Everything is mirrored to a rotating file so logs are readable while the server
    runs in another process (tail -f logs/app.log). TM_LOG_FILE="" disables that.
    """

    def __init__(self, settings: Settings) -> None:
        self._log_file = Path(settings.log_file) if settings.log_file else None

    def install(self) -> Path | None:
        """Install the handlers. Returns the log file path, or None when file logging is off.

        Safe to call more than once — handlers are replaced, not stacked.
        """
        handlers = self._handlers()
        for name in LOGGER_NAMES:
            self._replace_handlers(logging.getLogger(name), handlers)
        return self._log_file

    def _handlers(self) -> list[logging.Handler]:
        handlers = [self._console_handler()]
        if self._log_file is not None:
            handlers.append(self._file_handler(self._log_file))
        return handlers

    def _console_handler(self) -> logging.Handler:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%H:%M:%S"))
        return console

    def _file_handler(self, log_file: Path) -> logging.Handler:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
        )
        # The file spans days, so it carries the date too
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
        return handler

    def _replace_handlers(self, target: logging.Logger, handlers: list[logging.Handler]) -> None:
        for old in target.handlers[:]:
            target.removeHandler(old)
            old.close()
        for handler in handlers:
            target.addHandler(handler)
        target.setLevel(logging.INFO)
        target.propagate = False
