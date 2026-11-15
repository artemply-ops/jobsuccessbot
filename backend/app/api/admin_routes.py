"""Админка владельца: расходы на ИИ, запросы, воронка, деньги, ошибки за выбранный период.

Только для ADMIN_TG_IDS (billing.is_admin). Пользовательские тексты не отдаются: только счётчики, суммы и
имена шагов. Группировка по дням в Python, чтобы одинаково работать на Postgres и SQLite.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.billing_routes import _admin
from app.db import get_session
from app.services import attribution
from app.models import (BillingUsage, Identity, Job, LlmUsage, Package, Payment, Profile, PromoRedemption, Target,
                        User)

router = APIRouter(prefix="/api/admin")
USD_RUB = 100  # курс для оценки в рублях, тот же запас, что в docs/ECONOMICS.md


def _day(d: datetime) -> str:
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone(timedelta(hours=3))).date().isoformat()


@router.get("/stats")
async def stats(date_from: date | None = None, date_to: date | None = None, _: User = Depends(_admin),
                session: AsyncSession = Depends(get_session)):
    msk = timezone(timedelta(hours=3))
    date_to = date_to or datetime.now(msk).date()
    date_from = date_from or date_to - timedelta(days=29)
    if date_from > date_to or (date_to - date_from).days > 366:
        raise HTTPException(400, "Период от 1 дня до года")
    since = datetime.combine(date_from, time.min, msk)
    until = datetime.combine(date_to + timedelta(days=1), time.min, msk)

    def period(col):
        return (col >= since) & (col < until)

    days = [(date_from + timedelta(days=i)).isoformat() for i in range((date_to - date_from).days + 1)]
    series = {d: Counter() for d in days}

    # Расходы на ИИ
    usage = (await session.execute(select(LlmUsage.created_at, LlmUsage.action, LlmUsage.run, LlmUsage.model,
                                          LlmUsage.prompt_tokens, LlmUsage.completion_tokens, LlmUsage.cost_usd,
                                          LlmUsage.user_id).where(period(LlmUsage.created_at)))).all()
    runs: dict[tuple, float] = defaultdict(float)
    models: dict[str, Counter] = defaultdict(Counter)
    active = set()
    for at, action, run, model, pt, ct, cost, uid in usage:
        series[_day(at)]["cost_usd"] += cost or 0
        runs[(action or "other", run or f"single{at}")] += cost or 0
        m = models[model]
        m["calls"] += 1
        m["prompt_tokens"] += pt or 0
        m["completion_tokens"] += ct or 0
        m["cost_usd"] += cost or 0
        if uid:
            active.add(uid)
    by_action: dict[str, list[float]] = defaultdict(list)
    for (action, _), cost in runs.items():
        by_action[action].append(cost)
    total_usd = sum(c for (_, _), c in runs.items())

    # Запросы: пакеты, правки, вакансии, профили, ошибки
    pkgs = (await session.execute(select(Package.created_at, Package.version).where(period(Package.created_at)))).all()
    for at, ver in pkgs:
        series[_day(at)]["packages" if ver == 1 else "revisions"] += 1
    for (at,) in (await session.execute(select(Target.created_at).where(period(Target.created_at)))).all():
        series[_day(at)]["targets"] += 1
    jobs = (await session.execute(select(Job.created_at, Job.kind, Job.status, Job.note, Job.finished_at)
                                  .where(period(Job.created_at)))).all()
    durations: dict[str, list[float]] = defaultdict(list)
    errors = []
    for at, kind, status, note, fin in jobs:
        if status == "error":
            series[_day(at)]["errors"] += 1
            errors.append({"at": at.isoformat(), "kind": kind, "note": (note or "")[:200]})
        elif status == "done" and fin:
            durations[kind].append((fin - at).total_seconds())

    # Пользователи и каналы
    users = (await session.execute(select(User.id, User.created_at, User.tg_id, User.guest, User.consent_at,
                                          User.source).where(period(User.created_at)))).all()
    email_ids = set((await session.execute(select(Identity.user_id).where(Identity.provider == "email"))).scalars())
    channels = Counter()
    for uid, at, tg, guest, _c, _src in users:
        series[_day(at)]["new_users"] += 1
        channels["Гость (сайт)" if guest else "Telegram" if tg else "Почта" if uid in email_ids else "Другое"] += 1
    new_ids = [u[0] for u in users]

    async def has(model) -> set[int]:
        if not new_ids:
            return set()
        return set((await session.execute(select(model.user_id).where(model.user_id.in_(new_ids)))).scalars())

    with_profile, with_target, with_pkg = await has(Profile), await has(Target), await has(Package)
    paid_ids = set((await session.execute(select(Payment.user_id).where(Payment.status == "succeeded",
                                                                        Payment.user_id.in_(new_ids or [0])))).scalars())
    sources = await _sources(session, users, with_profile, with_pkg, paid_ids)
    funnel = [("Новые пользователи", len(users)), ("Дали согласие", sum(1 for u in users if u[4])),
              ("Собрали профиль", len(with_profile)), ("Добавили вакансию", len(with_target)),
              ("Получили документы", len(with_pkg)), ("Оплатили", len(paid_ids))]

    # Деньги
    pays = (await session.execute(select(Payment.paid_at, Payment.currency, Payment.amount)
                                  .where(Payment.status == "succeeded", period(Payment.paid_at)))).all()
    rub = sum(a for _, c, a in pays if c == "RUB")
    stars = sum(a for _, c, a in pays if c == "XTR")
    for at, c, a in pays:
        if c == "RUB":
            series[_day(at)]["revenue_rub"] += a
    promos = (await session.execute(select(func.count()).select_from(PromoRedemption)
                                    .where(period(PromoRedemption.created_at)))).scalar()
    billed = dict((await session.execute(select(BillingUsage.action, func.count())
                                         .where(period(BillingUsage.created_at)).group_by(BillingUsage.action))).all())

    n_pkg = sum(1 for _, v in pkgs if v == 1)
    pkg_cost = [c for a, c in ((a, c) for (a, _), c in runs.items()) if a == "package"]
    return {
        "from": date_from.isoformat(), "to": date_to.isoformat(), "usd_rub": USD_RUB,
        "kpi": {
            "users_total": (await session.execute(select(func.count()).select_from(User))).scalar(),
            "users_new": len(users), "users_active": len(active), "packages": n_pkg,
            "revisions": sum(1 for _, v in pkgs if v > 1), "targets": sum(s["targets"] for s in series.values()),
            "cost_usd": round(total_usd, 4), "cost_rub": round(total_usd * USD_RUB, 1),
            "cost_per_package_usd": round(sum(pkg_cost) / len(pkg_cost), 4) if pkg_cost else None,
            "revenue_rub": rub, "revenue_stars": stars, "promo_redemptions": promos,
            "errors": sum(s["errors"] for s in series.values()),
        },
        "series": [{"day": d, **{k: round(v, 4) if isinstance(v, float) else v for k, v in series[d].items()}}
                   for d in days],
        "actions": sorted([{"action": a, "runs": len(v), "total_usd": round(sum(v), 4),
                            "avg_usd": round(sum(v) / len(v), 4), "max_usd": round(max(v), 4)}
                           for a, v in by_action.items()], key=lambda r: -r["total_usd"]),
        "models": sorted([{"model": m, **{k: (round(v, 4) if k == "cost_usd" else int(v)) for k, v in c.items()}}
                          for m, c in models.items()], key=lambda r: -r["cost_usd"]),
        "durations": {k: {"runs": len(v), "avg_s": round(sum(v) / len(v)), "max_s": round(max(v))}
                      for k, v in durations.items()},
        "channels": dict(channels), "funnel": [{"step": s, "users": n} for s, n in funnel],
        "billed": billed, "sources": sources, "errors": sorted(errors, key=lambda e: e["at"], reverse=True)[:15],
    }


# ---------- Тексты сайта ----------

site = APIRouter(prefix="/api")


@site.get("/site/texts")
async def site_texts_public(session: AsyncSession = Depends(get_session)):
    """Без входа: тексты лендинга (последняя версия поверх умолчаний)."""
    from app.services import site_texts

    data, _ = await site_texts.current(session)
    return data


@router.get("/texts")
async def texts_get(_: User = Depends(_admin), session: AsyncSession = Depends(get_session)):
    from app.services import site_texts

    data, vid = await site_texts.current(session)
    return {"schema": site_texts.SCHEMA, "data": data, "version": vid, "placeholders": site_texts.PLACEHOLDERS,
            "warnings": site_texts.warnings(data), "history": await site_texts.history(session)}


class TextsIn(BaseModel):
    data: dict
    version: int | None = None  # какую версию редактировали: защита от затирания чужой правки


@router.put("/texts")
async def texts_put(body: TextsIn, user: User = Depends(_admin), session: AsyncSession = Depends(get_session)):
    from app.services import site_texts

    _, vid = await site_texts.current(session)
    if body.version != vid:
        raise HTTPException(409, "Тексты уже изменили в другой вкладке. Обновите страницу, чтобы не затереть правку")
    row = await site_texts.save(session, body.data, user.id)
    await session.commit()
    return {"version": row.id, "warnings": site_texts.warnings(row.data)}


@router.post("/texts/restore/{version_id}")
async def texts_restore(version_id: int, user: User = Depends(_admin), session: AsyncSession = Depends(get_session)):
    from app.services import site_texts

    try:
        row = await site_texts.restore(session, version_id, user.id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    await session.commit()
    return {"version": row.id}


async def _sources(session: AsyncSession, users, with_profile: set[int], with_pkg: set[int],
                   paid_ids: set[int]) -> list[dict]:
    """Когорта новых за период по меткам источника: сколько пришло, дошло до профиля и документов, заплатило
    и сколько принесло денег за всё время (оплаты не ограничены периодом, иначе поздняя оплата потеряется)."""
    by_user = {u[0]: u[5] for u in users}
    money: dict[int, Counter] = defaultdict(Counter)
    if by_user:
        for uid, cur, amount in (await session.execute(
                select(Payment.user_id, Payment.currency, Payment.amount)
                .where(Payment.status == "succeeded", Payment.user_id.in_(list(by_user))))).all():
            money[uid][cur] += amount or 0
    rows: dict[str, Counter] = defaultdict(Counter)
    for uid, src in by_user.items():
        r = rows[src or ""]
        r["users"] += 1
        r["profile"] += uid in with_profile
        r["documents"] += uid in with_pkg
        r["paid"] += uid in paid_ids
        r["revenue_rub"] += money[uid]["RUB"]
        r["revenue_stars"] += money[uid]["XTR"]
    out = [{"source": s or "direct", "channel": attribution.channel(s), **{k: int(v) for k, v in c.items()},
            "paid_rate": round(c["paid"] / c["users"], 3) if c["users"] else 0} for s, c in rows.items()]
    return sorted(out, key=lambda r: (-r["users"], r["source"]))
