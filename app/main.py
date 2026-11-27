import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.domain.model_policy import available_providers
from app.logging_setup import configure_logging
from app.usecases.maintenance import fail_records_left_by_previous_run
from app.web import demo_routes, routes
from app.web.shared import ApiError, api_error_response

STATIC_DIR = Path(__file__).parent / "static"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs when the server starts — not when the module is imported, so tests
    and tools that import the app never touch real records."""
    logger.info("=== startup ===")
    providers = [p["label"] for p in available_providers()]
    logger.info("Transcript Maker ready — providers: %s", ", ".join(providers) or "none (demo only)")
    logger.info("transcribe=%s  summarize=%s", settings.transcribe_model, settings.summarize_model)
    if app.state.log_file is not None:
        logger.info("log file: %s", app.state.log_file)
    fail_records_left_by_previous_run()
    yield


def create_app() -> FastAPI:
    log_file = configure_logging()

    app = FastAPI(title="Transcript Maker", lifespan=lifespan)
    app.state.log_file = log_file
    app.add_exception_handler(ApiError, api_error_response)
    app.include_router(routes.router)
    app.include_router(demo_routes.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index():
        html = (STATIC_DIR / "index.html").read_text()
        for name in ("app.js", "style.css"):
            path = STATIC_DIR / name
            if path.exists():
                stamp = int(path.stat().st_mtime)
                html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")
        return HTMLResponse(html)

    return app


app = create_app()
