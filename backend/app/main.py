import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import api
from app.config import get_settings
from app.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    task = None
    if get_settings().run_scheduler:
        from app.jobs import scheduler_loop

        task = asyncio.create_task(scheduler_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="JobPilot", lifespan=lifespan)
app.include_router(api)


@app.get("/health")
async def health():
    return {"ok": True}


dist = get_settings().web_dist
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        """PWA: статика и SPA-фолбэк на index.html."""
        f = (dist / path).resolve()
        if path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(dist / "index.html")
