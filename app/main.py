from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
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
        html = (STATIC_DIR / "index.html").read_text()
        for name in ("app.js", "style.css"):
            path = STATIC_DIR / name
            if path.exists():
                stamp = int(path.stat().st_mtime)
                html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")
        return HTMLResponse(html)

    return app


app = create_app()
