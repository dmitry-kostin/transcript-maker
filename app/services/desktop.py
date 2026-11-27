"""The desktop the server runs on (this is a local, single-user app)."""

import subprocess
from pathlib import Path


class Desktop:
    def reveal(self, path: Path) -> None:
        """Show ``path`` selected in Finder."""
        subprocess.Popen(["open", "-R", str(path)])
