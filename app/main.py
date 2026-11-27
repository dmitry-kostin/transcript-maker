"""The web application: ``Application()`` is the whole server, ready for uvicorn (``factory=True``)."""
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.logging_setup import LoggingSetup
from app.web.demo_routes import DemoWorkRoutes
from app.web.errors import ErrorResponses
from app.web.routes import ReadRoutes, RealWorkRoutes
from app.wiring import Wiring

STATIC_DIR = Path(__file__).parent / "static"
CACHE_BUSTED_ASSETS = ("app.js", "style.css")

logger = logging.getLogger(__name__)


class Application(FastAPI):
    """Nothing is built at import — only constructing this configures logging and wires the app.

    Startup work (the startup log, failing records a previous run left behind)
    happens when the server starts, not here, so tests and tools that build the
    app never touch real records.
    """

    def __init__(self) -> None:
        self._log_file = LoggingSetup(settings).install()
        self._wiring = Wiring(settings)
        super().__init__(title="Transcript Maker", lifespan=self._lifespan)
        self.state.log_file = self._log_file

        ErrorResponses().install(self)
        self._include_routes(self._wiring)
        self.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        self.add_api_route("/", self._index, methods=["GET"], response_class=HTMLResponse)

    def _include_routes(self, wiring: Wiring) -> None:
        self.include_router(ReadRoutes(wiring.records, wiring.providers, wiring.maintenance).router())
        self.include_router(RealWorkRoutes(wiring.transcription, wiring.summarization).router())
        self.include_router(DemoWorkRoutes(wiring.demo_transcription, wiring.demo_summarization).router())

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        logger.info("=== startup ===")
        providers = [provider["label"] for provider in self._wiring.providers.available()]
        logger.info("Transcript Maker ready — providers: %s", ", ".join(providers) or "none (demo only)")
        logger.info("transcribe=%s  summarize=%s", settings.transcribe_model, settings.summarize_model)
        if self._log_file is not None:
            logger.info("log file: %s", self._log_file)
        self._wiring.maintenance.fail_records_left_by_previous_run()
        yield

    async def _index(self) -> HTMLResponse:
        """index.html with its assets stamped by mtime, so a changed file is never served from cache."""
        html = (STATIC_DIR / "index.html").read_text()
        for name in CACHE_BUSTED_ASSETS:
            asset = STATIC_DIR / name
            if asset.exists():
                html = html.replace(f"/static/{name}", f"/static/{name}?v={int(asset.stat().st_mtime)}")
        return HTMLResponse(html)
