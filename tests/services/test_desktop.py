from pathlib import Path

from app.services.desktop import Desktop


class TestDesktop:
    def test_reveal_asks_finder_to_select_the_file(self, monkeypatch):
        commands = []
        monkeypatch.setattr("app.services.desktop.subprocess.Popen", commands.append)
        Desktop().reveal(Path("/some/record.md"))
        assert commands == [["open", "-R", "/some/record.md"]]
