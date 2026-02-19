#!/usr/bin/env python3
"""Launch the Transcript Maker server."""
import logging
import os
import sys


def main():
    sys.path.insert(0, os.path.dirname(__file__))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        from app.config import settings  # noqa: F401
    except Exception:
        logging.error("TM_OPENAI_API_KEY is not set.")
        print("  export TM_OPENAI_API_KEY=sk-...")
        print("  or create a .env file with: TM_OPENAI_API_KEY=sk-...")
        sys.exit(1)

    logger = logging.getLogger(__name__)
    logger.info("Starting Transcript Maker on http://127.0.0.1:8000")
    logger.info("temp_dir=%s  results_dir=%s", settings.temp_dir, settings.results_dir)

    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
