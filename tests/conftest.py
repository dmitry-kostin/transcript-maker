from pathlib import Path

import pytest


@pytest.fixture
def tmp_results(tmp_path, monkeypatch):
    """Provide a temporary results dir and patch history.RESULTS_DIR."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    import app.history as history_mod
    monkeypatch.setattr(history_mod, "RESULTS_DIR", results_dir)
    return results_dir


@pytest.fixture
def tmp_audio(tmp_path):
    """Create a small dummy audio file for chunking tests."""
    audio_file = tmp_path / "test_abc12345.mp3"
    # Write 1 MB of zeros (not real audio, but enough for size-based logic)
    audio_file.write_bytes(b"\x00" * (1024 * 1024))
    return audio_file
