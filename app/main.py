import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

_formatter = logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
_handler = logging.StreamHandler()
_handler.setFormatter(_formatter)

for _name in ("app", "uvicorn", "uvicorn.error", "uvicorn.access"):
    _lg = logging.getLogger(_name)
    _lg.handlers.clear()
    _lg.addHandler(_handler)
    _lg.setLevel(logging.INFO)
    _lg.propagate = False
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.history import cleanup_stale_records

STATIC_DIR = Path(__file__).parent / "static"


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="Transcript Maker")

    from app.config import settings
    providers = []
    if settings.openai_api_key:
        providers.append("OpenAI")
    if settings.google_api_key:
        providers.append("Gemini")
    logger.info("Transcript Maker ready — providers: %s", ", ".join(providers) or "none (demo only)")
    logger.info("transcribe=%s  summarize=%s", settings.transcribe_model, settings.summarize_model)

    cleanup_stale_records()
    app.include_router(router)
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
