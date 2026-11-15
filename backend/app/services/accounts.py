"""Учётки и способы входа. Один кабинет (users.id) на три канала: сайт, Mini App, бот.

Способы входа: Telegram (users.tg_id), почта и Яндекс ID (таблица identities). Человек может войти любым
из привязанных способов и попадёт в тот же кабинет. Гость: начал работу на сайте без входа, при входе его
данные переезжают в учётку, в которую он вошёл (merge).

Правило объединения: ничего не удаляется. Материалы, вакансии, пакеты, оплаты и тарифы складываются.
Профили обеих учёток остаются версиями, актуальной становится самая свежая. Бесплатный старт один на человека.
"""

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import Base
from app.services import attribution
from app.models import (BillingUsage, CompanyScore, Contact, Grant, Identity, LlmUsage, LoginCode, LoginToken,
                        Profile, User, VacancyScore)

CODE_TTL = timedelta(minutes=10)
CODE_ATTEMPTS = 5
CODE_RESEND = timedelta(seconds=60)
CODES_PER_HOUR = 5
TOKEN_TTL = timedelta(minutes=15)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class AccountError(ValueError):
    """Понятная пользователю ошибка входа или привязки."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)  # SQLite отдаёт время без зоны


def _h(value: str) -> str:
    return hmac.new(get_settings().jwt_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def norm_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not EMAIL_RE.match(e) or len(e) > 254:
        raise AccountError("Проверьте адрес почты")
    return e


def is_guest(user: User) -> bool:
    return bool(user.guest)


# ---------- Гость ----------

_guest_hits: dict[str, list[float]] = defaultdict(list)


async def create_guest(session: AsyncSession, ip: str, source: str | None = None) -> User:
    """Гостевая учётка для работы на сайте без входа. Ограничение по IP от накрутки бесплатных действий."""
    day = time.time() - 86400
    hits = [t for t in _guest_hits[ip] if t > day]
    if len(hits) >= get_settings().guests_per_ip_day:
        raise AccountError("Слишком много новых сессий с этого адреса. Войдите по почте или через Telegram")
    hits.append(time.time())
    _guest_hits[ip] = hits
    user = User(guest=True, notify_threshold=get_settings().notify_threshold)
    attribution.assign(user, source)
    session.add(user)
    await session.flush()
    return user


# ---------- Код на почту ----------

async def request_code(session: AsyncSession, email: str) -> str:
    """Создаёт код и возвращает его (отправка письма в вызывающем коде). Лимиты: раз в минуту, 5 в час."""
    e = norm_email(email)
    eh = _h(e)
    rows = (await session.execute(select(LoginCode).where(LoginCode.email_hash == eh,
                                                          LoginCode.created_at > _now() - timedelta(hours=1))
                                  .order_by(LoginCode.created_at.desc()))).scalars().all()
    if rows and _now() - _aware(rows[0].created_at) < CODE_RESEND:
        raise AccountError("Код уже отправлен. Новый можно запросить через минуту")
    if len(rows) >= CODES_PER_HOUR:
        raise AccountError("Слишком много попыток. Попробуйте через час")
    code = f"{secrets.randbelow(1_000_000):06d}"
    session.add(LoginCode(email_hash=eh, code_hash=_h(f"{e}:{code}"), expires_at=_now() + CODE_TTL))
    await session.flush()
    return code


async def check_code(session: AsyncSession, email: str, code: str) -> str:
    """Проверяет код, возвращает нормализованную почту. Пять ошибок гасят код."""
    e = norm_email(email)
    row = (await session.execute(select(LoginCode).where(LoginCode.email_hash == _h(e), LoginCode.used.is_(False))
                                 .order_by(LoginCode.created_at.desc()).limit(1))).scalar_one_or_none()
    if row is None or _aware(row.expires_at) < _now() or row.attempts >= CODE_ATTEMPTS:
        raise AccountError("Код устарел. Запросите новый")
    if not hmac.compare_digest(row.code_hash, _h(f"{e}:{(code or '').strip()}")):
        row.attempts += 1
        await session.flush()
        raise AccountError("Неверный код")
    row.used = True
    return e


# ---------- Одноразовые ссылки ----------

async def issue_link_token(session: AsyncSession, user: User | None, purpose: str = "login") -> str:
    token = secrets.token_urlsafe(24)
    session.add(LoginToken(token_hash=_h(token), user_id=user.id if user else None, purpose=purpose,
                           expires_at=_now() + TOKEN_TTL))
    await session.flush()
    return token


async def use_link_token(session: AsyncSession, token: str, purpose: str = "login") -> User | None:
    row = (await session.execute(select(LoginToken).where(LoginToken.token_hash == _h(token or ""))))\
        .scalar_one_or_none()
    if row is None or row.used or row.purpose != purpose or _aware(row.expires_at) < _now():
        raise AccountError("Ссылка устарела. Получите новую")
    row.used = True
    return await session.get(User, row.user_id) if row.user_id else None


# ---------- Вход и привязка ----------

async def login_identity(session: AsyncSession, provider: str, subject: str, current: User | None,
                         source: str | None = None) -> User:
    """Вход по почте или Яндекс ID. current: кто уже вошёл (гость или пользователь Mini App, который привязывает
    способ входа). Возвращает учётку, под которой человек продолжит работу."""
    found = (await session.execute(select(Identity).where(Identity.provider == provider,
                                                          Identity.subject == subject))).scalar_one_or_none()
    if found:
        owner = await session.get(User, found.user_id)
        return await _join(session, current, owner)
    user = current or User(notify_threshold=get_settings().notify_threshold)
    if current is None:
        attribution.assign(user, source)
        session.add(user)
        await session.flush()
    session.add(Identity(user_id=user.id, provider=provider, subject=subject))
    user.guest = None
    await session.flush()
    return user


async def login_telegram(session: AsyncSession, tg_id: int, username: str | None, first_name: str | None,
                         current: User | None, source: str | None = None) -> User:
    """Вход через Telegram на сайте (виджет). Если человек уже вошёл (гость или по почте), Telegram привязывается
    к его кабинету; если у этого Telegram был свой кабинет, кабинеты объединяются."""
    from app.auth import upsert_tg_user

    owner = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if current is None or owner is not None and owner.id == current.id:
        return await upsert_tg_user(session, tg_id, username, first_name, source)
    if owner is None:
        if current.tg_id and current.tg_id != tg_id:
            raise AccountError("К этому кабинету уже привязан другой Telegram")
        current.tg_id, current.guest = tg_id, None
        current.username, current.first_name = username or current.username, first_name or current.first_name
        await session.flush()
        return current
    return await _join(session, current, owner)


async def _join(session: AsyncSession, current: User | None, owner: User) -> User:
    """current вошёл способом, который принадлежит owner. Гость вливается в owner, иначе owner в current."""
    if current is None or current.id == owner.id:
        return owner
    if is_guest(current):
        await merge(session, keep=owner, drop=current)
        return owner
    await merge(session, keep=current, drop=owner)
    return current


async def methods(session: AsyncSession, user: User) -> dict:
    ids = (await session.execute(select(Identity).where(Identity.user_id == user.id))).scalars().all()
    email = next((i.subject for i in ids if i.provider == "email"), None)
    return {"telegram": user.tg_id is not None, "telegram_username": user.username if user.tg_id else None,
            "email": email, "yandex": any(i.provider == "yandex" for i in ids), "guest": is_guest(user)}


# ---------- Объединение кабинетов ----------

_SPECIAL = {"users", "contacts", "profiles", "vacancy_scores", "company_scores", "grants"}


async def merge(session: AsyncSession, keep: User, drop: User) -> None:
    """Переносит всё из drop в keep и удаляет drop. Ничего из данных пользователя не теряется."""
    if keep.id == drop.id:
        return
    if keep.tg_id and drop.tg_id and keep.tg_id != drop.tg_id:
        raise AccountError("Оба кабинета привязаны к разным Telegram. Объединить их нельзя")
    # Источник: остаётся метка более раннего кабинета (первое касание)
    older, newer = sorted((keep, drop), key=lambda u: (u.created_at is None, u.created_at or datetime.min, u.id))
    keep.source = older.source or newer.source
    await session.flush()

    # Профили: обе истории сохраняются версиями, порядок по времени создания, актуальная самая свежая
    profiles = (await session.execute(select(Profile).where(Profile.user_id.in_([keep.id, drop.id]))
                                      .order_by(Profile.created_at, Profile.id))).scalars().all()
    for i, p in enumerate(profiles):
        p.version, p.user_id = -(i + 1), keep.id  # временные номера, чтобы не задеть уникальность
    await session.flush()
    for p in profiles:
        p.version = -p.version
    # Оценки вакансий и компаний привязаны к версии профиля, пересчитываются сами (поиск сейчас выключен)
    for model in (VacancyScore, CompanyScore):
        await session.execute(delete(model).where(model.user_id.in_([keep.id, drop.id])))

    # Контакты: у keep остаются свои, пустые поля заполняются из drop
    ck, cd = await session.get(Contact, keep.id), await session.get(Contact, drop.id)
    if cd is not None:
        if ck is None:
            ck = Contact(user_id=keep.id, links=[], vault={})
            session.add(ck)
        for f in ("full_name", "email", "phone", "city", "photo_path"):
            if not getattr(ck, f) and getattr(cd, f):
                setattr(ck, f, getattr(cd, f))
        ck.links = list(dict.fromkeys([*(ck.links or []), *(cd.links or [])]))
        ck.vault = {**(cd.vault or {}), **(ck.vault or {})}
        await session.delete(cd)
        await session.flush()

    # Тарифы и покупки переезжают; бесплатный старт один: расход второго переносится на первый
    free_keep = (await session.execute(select(Grant).where(Grant.user_id == keep.id, Grant.source == "free")
                                       .limit(1))).scalar_one_or_none()
    for g in (await session.execute(select(Grant).where(Grant.user_id == drop.id))).scalars().all():
        if g.source == "free" and free_keep is not None:
            await session.execute(update(BillingUsage).where(BillingUsage.grant_id == g.id)
                                  .values(grant_id=free_keep.id))
            await session.delete(g)
        else:
            g.user_id = keep.id
    await session.flush()

    # Остальные таблицы со ссылкой на пользователя: переносим, дубли по уникальным ключам отбрасываем
    for table in Base.metadata.sorted_tables:
        if table.name in _SPECIAL:
            continue
        cols = [c for c in table.columns if any(fk.column.table.name == "users" for fk in c.foreign_keys)]
        if table.name == LlmUsage.__tablename__:
            cols = [table.c.user_id]
        for col in cols:
            keys = [[c for c in uc.columns if c is not col] for uc in table.constraints
                    if getattr(uc, "columns", None) is not None and col in list(uc.columns)
                    and uc.__class__.__name__ in ("UniqueConstraint", "PrimaryKeyConstraint")
                    and len(uc.columns) > 1]
            for other in keys:  # строка drop конфликтует со строкой keep по тем же остальным колонкам
                k = table.alias("k")
                dup = select(func.count()).select_from(k).where(
                    *[k.c[o.name] == o for o in other], k.c[col.name] == keep.id).scalar_subquery()
                await session.execute(delete(table).where(col == drop.id, dup > 0))
            await session.execute(update(table).where(col == drop.id).values({col.name: keep.id}))

    # Учётка: Telegram, согласие, имя
    tg = drop.tg_id
    keep.consent_at = min([_aware(d) for d in (keep.consent_at, drop.consent_at) if d], default=None)
    keep.username, keep.first_name = keep.username or drop.username, keep.first_name or drop.first_name
    keep.guest = None
    drop.tg_id = None
    await session.flush()
    if tg and not keep.tg_id:
        keep.tg_id = tg
    await session.delete(drop)
    await session.flush()
