"""Чистка брошенных гостевых кабинетов (Т4).

Гость начал работу на сайте без входа и не вернулся. Через GUEST_TTL_DAYS без активности его кабинет удаляется
целиком: материалы, профили, пакеты, задачи, файлы на диске. Это и рост таблиц, и хранение чужих ПДн без цели.

Не трогаем: кабинеты со входом (guest не True, есть tg_id или способ входа), кабинеты с любой оплатой
(платёж это бухгалтерский документ), кабинеты с активностью за последние GUEST_TTL_DAYS.
Расход ИИ (llm_usage, только числа) остаётся для экономики, без привязки к пользователю.
"""

import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import Base
from app.models import Company, Contact, Identity, LlmUsage, Payment, Source, User, Vacancy

log = logging.getLogger(__name__)

GUEST_TTL_DAYS = 30
BATCH = 200


def _user_tables():
    """Таблицы со ссылкой на users и их колонки; дочерние раньше родительских (billing_usage до grants)."""
    out = []
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in ("users", LlmUsage.__tablename__, Company.__tablename__):
            continue
        cols = [c for c in table.columns if any(fk.column.table.name == "users" for fk in c.foreign_keys)]
        if cols:
            out.append((table, cols))
    return out


async def stale_guest_ids(session: AsyncSession, days: int = GUEST_TTL_DAYS, limit: int = BATCH) -> list[int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(User.id).where(User.guest.is_(True), User.tg_id.is_(None), User.created_at < cutoff,
                              ~exists().where(Payment.user_id == User.id),
                              ~exists().where(Identity.user_id == User.id))
    for table, cols in _user_tables():  # активность: любая запись пользователя новее порога
        if "created_at" in table.c:
            q = q.where(~exists().where(or_(*[c == User.id for c in cols]), table.c.created_at >= cutoff))
    return list((await session.execute(q.order_by(User.id).limit(limit))).scalars())


async def purge_users(session: AsyncSession, ids: list[int]) -> int:
    """Удаляет кабинеты целиком. Вызывающий отвечает за то, что это брошенные гости."""
    if not ids:
        return 0
    files = set()
    files |= {p for p in (await session.execute(select(Source.path).where(Source.user_id.in_(ids)))).scalars() if p}
    files |= {p for p in (await session.execute(select(Contact.photo_path).where(Contact.user_id.in_(ids))))
              .scalars() if p}

    await session.execute(update(LlmUsage).where(LlmUsage.user_id.in_(ids)).values(user_id=None))
    await session.execute(update(Company).where(Company.added_by_user_id.in_(ids)).values(added_by_user_id=None))
    for table, cols in _user_tables():
        await session.execute(delete(table).where(or_(*[c.in_(ids) for c in cols])))
    # Вакансии-заглушки быстрого пути «резюме из опыта» (intake.general_target): general:<uid>:<uuid>
    await session.execute(delete(Vacancy).where(or_(*[Vacancy.fingerprint.like(f"general:{i}:%") for i in ids])))
    await session.execute(delete(User).where(User.id.in_(ids)))
    await session.flush()

    d = get_settings().files_dir
    for uid in ids:  # файлы пользователя называются <id>_..., «4_» не совпадает с «44_»
        files |= {str(p) for p in d.glob(f"{uid}_*")}
    for f in files:
        p = Path(f)
        try:
            (p if p.is_absolute() else d / p).unlink(missing_ok=True)
        except OSError:
            log.warning("cleanup: file not removed")
    return len(ids)


async def purge_stale_guests(session: AsyncSession, days: int = GUEST_TTL_DAYS) -> int:
    total = 0
    while ids := await stale_guest_ids(session, days):
        total += await purge_users(session, ids)
        await session.commit()
    return total
