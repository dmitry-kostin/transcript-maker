#!/usr/bin/env python3
"""Launch the Transcript Maker server."""

import logging
import os
import sys

HOST = "127.0.0.1"
PORT = 8000


class Launcher:
    def run(self) -> None:
        sys.path.insert(0, os.path.dirname(__file__))
        settings = self._load_settings()

        logger = logging.getLogger(__name__)
        logger.info("Starting Transcript Maker on http://%s:%d", HOST, PORT)
        logger.info("temp_dir=%s  results_dir=%s", settings.temp_dir, settings.results_dir)

        import uvicorn

        uvicorn.run("app.main:Application", factory=True, host=HOST, port=PORT, log_level="info", log_config=None)

    def _load_settings(self):
        try:
            from app.config import settings
        except Exception:
            logging.error("TM_OPENAI_API_KEY is not set.")
            print("  export TM_OPENAI_API_KEY=sk-...")
            print("  or create a .env file with: TM_OPENAI_API_KEY=sk-...")
            sys.exit(1)
        return settings


if __name__ == "__main__":
    Launcher().run()
