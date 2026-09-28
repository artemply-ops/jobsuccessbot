"""Сбор вакансий из источников, склейка дублей, привязка к каталогу компаний."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ROOT
from app.models import Company, Vacancy
from app.schemas import ProfileData
from app.services.pii import EMAIL_RE, PHONE_RE
from app.services.sources import careers, superjob, trudvsem
from app.services.sources.base import RawVacancy, fingerprint, norm

log = logging.getLogger(__name__)
SEED_FILE = ROOT / "data" / "companies_seed.yaml"


def _clean(text: str) -> str:
    """Контакты рекрутеров из описаний вакансий не храним и в LLM не отправляем."""
    return EMAIL_RE.sub("[EMAIL]", PHONE_RE.sub("[PHONE]", text or ""))[:20_000]


async def upsert(session: AsyncSession, raws: list[RawVacancy]) -> tuple[int, int]:
    """Возвращает (новых, обновлённых)."""
    companies = (await session.execute(select(Company))).scalars().all()
    by_domain = {c.domain: c for c in companies}
    by_name = {norm(c.name): c for c in companies}
    created = updated = 0
    for r in raws:
        if not r.title or not r.url:
            continue
        fp = fingerprint(r)
        v = (await session.execute(select(Vacancy).where(Vacancy.fingerprint == fp))).scalar_one_or_none()
        src = {"source": r.source, "external_id": r.external_id, "url": r.url}
        company = by_domain.get(r.company_domain or "") or by_name.get(norm(r.company_name))
        if v is None:
            v = Vacancy(fingerprint=fp, company_id=company.id if company else None, company_name=r.company_name,
                        title=r.title, city=r.city, remote=r.remote, salary_from=r.salary_from,
                        salary_to=r.salary_to, currency=r.currency, description=_clean(r.description),
                        url=r.url, sources=[src], published_at=r.published_at)
            session.add(v)
            created += 1
        else:
            if src not in v.sources:
                v.sources = [*v.sources, src]
            if len(r.description or "") > len(v.description or ""):
                v.description = _clean(r.description)
            v.salary_from = v.salary_from or r.salary_from
            v.salary_to = v.salary_to or r.salary_to
            v.remote = v.remote if v.remote is not None else r.remote
            v.fetched_at = datetime.now(timezone.utc)
            v.active = True
            updated += 1
    await session.flush()
    return created, updated


def queries_for_profile(data: dict) -> list[str]:
    p = ProfileData.model_validate(data)
    qs = list(dict.fromkeys(r.strip() for r in p.target_roles if r.strip()))[:3]
    if not qs and p.headline:
        qs = [p.headline]
    return qs


async def collect_by_queries(session: AsyncSession, queries: list[str]) -> dict:
    stats = {"queries": queries, "created": 0, "updated": 0, "errors": []}
    async with httpx.AsyncClient(timeout=30) as client:
        for q in queries:
            raws: list[RawVacancy] = []
            for name, coro in (("trudvsem", trudvsem.search(q, client=client)), ("superjob", superjob.search(q))):
                try:
                    raws += await coro
                except Exception as e:  # один источник упал, остальные продолжаем
                    stats["errors"].append(f"{name}: {type(e).__name__}")
            c, u = await upsert(session, raws)
            stats["created"] += c
            stats["updated"] += u
    return stats


async def collect_companies(session: AsyncSession, only_ids: list[int] | None = None) -> dict:
    q = select(Company).where(Company.status == "active", Company.moderated.is_(True))
    if only_ids:
        q = q.where(Company.id.in_(only_ids))
    stats = {"companies": 0, "created": 0, "updated": 0, "errors": []}
    for comp in (await session.execute(q)).scalars().all():
        try:
            if comp.ats_type == "lever":
                raws = await careers.fetch_lever(comp.ats_token, comp.name)
            elif comp.ats_type == "greenhouse":
                raws = await careers.fetch_greenhouse(comp.ats_token, comp.name)
            elif comp.ats_type == "jsonld":
                raws = await careers.fetch_jsonld(comp.careers_url, comp.name)
            elif comp.ats_type == "html":
                raws = await careers.fetch_html_llm(comp.careers_url, comp.name)
            else:
                continue
        except Exception as e:
            stats["errors"].append(f"{comp.domain}: {type(e).__name__}")
            continue
        for r in raws:
            r.company_domain = comp.domain
        c, u = await upsert(session, raws)
        stats["companies"] += 1
        stats["created"] += c
        stats["updated"] += u
        await session.commit()
    return stats


async def discover_companies(session: AsyncSession, limit: int = 20, concurrency: int = 5) -> dict:
    q = select(Company).where(Company.status == "unverified").limit(limit)
    items = (await session.execute(q)).scalars().all()
    sem = asyncio.Semaphore(concurrency)

    async def one(c: Company):
        async with sem:
            return c, await careers.discover(c.domain, c.careers_url)

    stats: dict[str, int] = {}
    for comp, d in await asyncio.gather(*(one(c) for c in items)):
        comp.careers_url = d.careers_url or comp.careers_url
        comp.ats_type, comp.ats_token = d.ats_type, d.ats_token
        comp.status, comp.status_note = d.status, d.note
        comp.last_checked_at = datetime.now(timezone.utc)
        stats[d.status] = stats.get(d.status, 0) + 1
    await session.flush()
    return stats


async def load_seed(session: AsyncSession, path: Path = SEED_FILE) -> int:
    """Загружает посевной каталог. Уже существующие домены не трогает."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    existing = set((await session.execute(select(Company.domain))).scalars())
    added = 0
    for segment, items in data.items():
        for item in items:
            domain = item["domain"].lower()
            if domain in existing:
                c = (await session.execute(select(Company).where(Company.domain == domain))).scalar_one()
                if segment not in c.segments:
                    c.segments = [*c.segments, segment]
                continue
            session.add(Company(name=item["name"], domain=domain, segments=[segment],
                                description=item.get("description"), careers_url=item.get("careers_url")))
            existing.add(domain)
            added += 1
    await session.flush()
    return added
