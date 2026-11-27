import os

import pytest

# Tests import app.main, which installs a file log handler — disable it so test
# runs don't append to logs/app.log. Must happen before app.config is imported.
os.environ.setdefault("TM_LOG_FILE", "")


@pytest.fixture
def tmp_results(tmp_path, monkeypatch):
    """Provide a temporary results dir and patch history.RESULTS_DIR."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    import app.storage.history as history_mod
    monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
    return results_dir


@pytest.fixture
def tmp_audio(tmp_path):
    """Create a small dummy audio file for chunking tests."""
    audio_file = tmp_path / "test_abc12345.mp3"
    # Write 1 MB of zeros (not real audio, but enough for size-based logic)
    audio_file.write_bytes(b"\x00" * (1024 * 1024))
    return audio_file


@pytest.fixture
def client(tmp_results):
    """TestClient on a temporary results dir. Startup (lifespan) does not run."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    return TestClient(create_app())
