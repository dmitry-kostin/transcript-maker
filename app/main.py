import logging
import logging.handlers
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.config import settings

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
LOG_MAX_BYTES = 5_000_000
LOG_BACKUP_COUNT = 3

_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%H:%M:%S"))
_handlers: list[logging.Handler] = [_handler]

# Mirror everything to a rotating log file so logs are readable while the
# server runs in another process (tail -f logs/app.log). TM_LOG_FILE="" disables.
LOG_FILE: Path | None = Path(settings.log_file) if settings.log_file else None
if LOG_FILE is not None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    _handlers.append(_file_handler)

for _name in ("app", "uvicorn", "uvicorn.error", "uvicorn.access"):
    _lg = logging.getLogger(_name)
    _lg.handlers.clear()
    for _h in _handlers:
        _lg.addHandler(_h)
    _lg.setLevel(logging.INFO)
    _lg.propagate = False
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.history import cleanup_stale_records

STATIC_DIR = Path(__file__).parent / "static"


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="Transcript Maker")

    logger.info("=== startup ===")

    providers = []
    if settings.openai_api_key:
        providers.append("OpenAI")
    if settings.google_api_key:
        providers.append("Gemini")
    logger.info("Transcript Maker ready — providers: %s", ", ".join(providers) or "none (demo only)")
    logger.info("transcribe=%s  summarize=%s", settings.transcribe_model, settings.summarize_model)
    if LOG_FILE is not None:
        logger.info("log file: %s", LOG_FILE)

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
