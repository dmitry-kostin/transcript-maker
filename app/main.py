from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.history import cleanup_stale_records

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Transcript Maker")
    cleanup_stale_records()
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
