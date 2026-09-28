"""Фоновые задачи в процессе приложения. Статус хранится в таблице jobs.

Для MVP этого достаточно. При росте нагрузки меняется на Arq + Redis без изменения сервисов.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Profile, User
from app.services import collect, notify, profile, scoring
from app.services.llm import LLMError

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()

JobFn = Callable[[AsyncSession], Awaitable[dict]]


async def enqueue(kind: str, user_id: int | None, fn: JobFn) -> int:
    from app.models import Job

    async with SessionLocal() as s:
        job = Job(kind=kind, user_id=user_id)
        s.add(job)
        await s.commit()
        job_id = job.id

    async def runner():
        async with SessionLocal() as s:
            j = await s.get(Job, job_id)
            j.status = "running"
            await s.commit()
            try:
                result = await fn(s)
                await s.commit()
                j.status, j.result = "done", result or {}
            except (ValueError, LLMError) as e:
                await s.rollback()
                j = await s.get(Job, job_id)
                j.status, j.note = "error", str(e)
            except Exception as e:
                log.exception("job %s failed", job_id)
                await s.rollback()
                j = await s.get(Job, job_id)
                j.status, j.note = "error", f"Внутренняя ошибка: {type(e).__name__}"
            j.finished_at = datetime.now(timezone.utc)
            await s.commit()

    t = asyncio.create_task(runner())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return job_id


# ---------- Сценарии ----------

def build_profile_job(user_id: int, source_ids: list[int] | None = None) -> JobFn:
    async def fn(s: AsyncSession) -> dict:
        from app.models import Source

        user = await s.get(User, user_id)
        sources = None
        if source_ids:
            sources = list((await s.execute(select(Source).where(Source.id.in_(source_ids)))).scalars())
        p = await profile.build_profile(s, user, sources)
        return {"profile_version": p.version, "questions": len(p.questions)}
    return fn


def search_job(user_id: int) -> JobFn:
    """Полный поиск для пользователя: сбор по ролям -> скоринг вакансий и компаний -> уведомление."""
    async def fn(s: AsyncSession) -> dict:
        prof = await profile.latest_profile(s, user_id)
        if prof is None:
            raise ValueError("Сначала соберите профиль")
        queries = collect.queries_for_profile(prof.data)
        c = await collect.collect_by_queries(s, queries)
        await s.commit()
        sc = await scoring.score_user(s, user_id)
        cs = await scoring.score_companies(s, user_id)
        sent = await notify.notify_new_matches(s, user_id)
        return {"collect": c, "score": sc, "companies": cs, "notified": sent}
    return fn


def package_job(user_id: int, vacancy_id: int, instruction: str | None) -> JobFn:
    async def fn(s: AsyncSession) -> dict:
        from app.services.package import build_package

        pkg = await build_package(s, user_id, vacancy_id, instruction)
        return {"package_id": pkg.id, "version": pkg.version, "flags": len(pkg.fact_flags)}
    return fn


async def daily_refresh() -> dict:
    """Ежедневно: проверка каталога, сбор с карьерных страниц, пересчёт для всех пользователей с профилем."""
    async with SessionLocal() as s:
        d = await collect.discover_companies(s, limit=50)
        await s.commit()
        c = await collect.collect_companies(s)
        await s.commit()
        user_ids = set((await s.execute(select(Profile.user_id))).scalars())
    for uid in user_ids:
        try:
            async with SessionLocal() as s:
                await search_job(uid)(s)
                await s.commit()
        except Exception:
            log.exception("daily refresh failed for user %s", uid)
    return {"discover": d, "companies": c, "users": len(user_ids)}


async def scheduler_loop(interval_hours: int = 24) -> None:
    while True:
        try:
            log.info("daily refresh: %s", await daily_refresh())
        except Exception:
            log.exception("daily refresh failed")
        await asyncio.sleep(interval_hours * 3600)
