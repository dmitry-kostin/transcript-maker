import logging

import pytest

from app.config import settings
from app.logging_setup import LOGGER_NAMES, LoggingSetup


@pytest.fixture(autouse=True)
def _restore_handlers():
    saved = {name: logging.getLogger(name).handlers[:] for name in LOGGER_NAMES}
    yield
    for name, handlers in saved.items():
        logging.getLogger(name).handlers[:] = handlers


class TestLoggingSetup:
    def test_file_logging_can_be_switched_off(self, monkeypatch):
        monkeypatch.setattr(settings, "log_file", "")
        assert LoggingSetup(settings).install() is None
        assert len(logging.getLogger("app").handlers) == 1

    def test_mirrors_to_the_log_file(self, monkeypatch, tmp_path):
        log_file = tmp_path / "logs" / "app.log"
        monkeypatch.setattr(settings, "log_file", str(log_file))
        assert LoggingSetup(settings).install() == log_file
        logging.getLogger("app.test").info("hello file")
        for handler in logging.getLogger("app").handlers:
            handler.flush()
        assert "hello file" in log_file.read_text()

    def test_installing_twice_replaces_handlers(self, monkeypatch):
        monkeypatch.setattr(settings, "log_file", "")
        LoggingSetup(settings).install()
        LoggingSetup(settings).install()
        for name in LOGGER_NAMES:
            assert len(logging.getLogger(name).handlers) == 1
            assert logging.getLogger(name).propagate is False
