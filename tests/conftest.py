import os
from pathlib import Path

import pytest

# Building the app installs a file log handler — disable it so test runs don't
# append to logs/app.log. Must happen before app.config is imported.
os.environ.setdefault("TM_LOG_FILE", "")


@pytest.fixture
def tmp_results(tmp_path, monkeypatch) -> Path:
    """A temporary results dir: everything built from ``settings`` stores its records there."""
    from app.config import settings
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(settings, "results_dir", str(results_dir))
    return results_dir


@pytest.fixture
def history(tmp_results):
    """Records, summaries, audio cache and chunk cache in the temporary results dir."""
    from app.config import settings
    from app.storage.history import History
    return History.in_results_dir(settings)


@pytest.fixture
def tmp_audio(tmp_path) -> Path:
    """A small dummy audio file for chunking tests."""
    audio_file = tmp_path / "test_abc12345.mp3"
    # Write 1 MB of zeros (not real audio, but enough for size-based logic)
    audio_file.write_bytes(b"\x00" * (1024 * 1024))
    return audio_file


@pytest.fixture
def client(tmp_results):
    """TestClient on a temporary results dir. Startup (lifespan) does not run."""
    from fastapi.testclient import TestClient

    from app.main import Application
    return TestClient(Application())
