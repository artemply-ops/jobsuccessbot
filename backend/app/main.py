import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import api
from app.config import get_settings
from app.db import init_db
from app.logs import setup_logging

setup_logging()
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    s = get_settings()
    tasks = []
    if s.run_scheduler:
        from app.jobs import scheduler_loop

        tasks.append(asyncio.create_task(scheduler_loop()))
    if s.run_bot_in_api and s.telegram_bot_token:
        from app.bot.handlers import run_polling

        async def bot_forever():
            # Падение или зависание старта бота раньше было молчаливым: API жив, бот нет. Теперь в лог и повтор.
            while True:
                try:
                    await run_polling()
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("bot polling crashed, restart in 15 s")
                    await asyncio.sleep(15)

        tasks.append(asyncio.create_task(bot_forever()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="JobPilot", lifespan=lifespan)
app.include_router(api)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Необработанная ошибка API: в лог с трассировкой (метод и путь, без тела запроса), клиенту 500."""
    log.error("unhandled %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": "Внутренняя ошибка. Мы уже видим её в логах."}, status_code=500)


@app.get("/health")
async def health():
    return {"ok": True}


dist = get_settings().web_dist
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    # index.html, service worker и манифест всегда перепроверяются: без этого окно Telegram и установленная PWA
    # держали старую сборку после выкатки (14.11.2026). Файлы в /assets с хэшем в имени кэшируются навсегда.
    NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

    @app.middleware("http")
    async def immutable_assets(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/assets/") and resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @app.get("/{path:path}")
    async def spa(path: str):
        """PWA: статика и SPA-фолбэк на index.html."""
        f = (dist / path).resolve()
        if path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f, headers=NO_CACHE if f.suffix in (".js", ".webmanifest", ".html") else None)
        return FileResponse(dist / "index.html", headers=NO_CACHE)
