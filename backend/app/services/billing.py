"""Биллинг: бесплатный старт, разовые покупки, тарифы, промокоды. Одно место для цен и правил.

Грант это набор лимитов {действие: количество} со сроком или без. Списание пишется в billing_usage со ссылкой
на грант; остаток = лимит − списания. Новый продукт или акция: строка в PRODUCTS, без миграций.
Экономика и обоснование цен: docs/ECONOMICS.md. Архитектура: docs/ARCHITECTURE.md.
"""

import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BillingUsage, Grant, Payment, PromoCode, PromoRedemption, User

ACTIONS = ("package", "revision")  # платные действия; всё остальное бесплатно
ACTION_TITLES = {"package": "пакет под вакансию", "revision": "правка"}
ACTION_VERBS = {"package": "собрать новый пакет", "revision": "внести правку"}


@dataclass(frozen=True)
class Product:
    sku: str
    title: str
    desc: str
    price_rub: int
    quota: dict
    days: int | None = None  # None: бессрочно

    @property
    def price_stars(self) -> int:
        return max(1, math.ceil(self.price_rub / get_settings().star_rub))


PRODUCTS: dict[str, Product] = {p.sku: p for p in [
    Product("revision", "Правка", "Одна правка готового пакета", 29, {"revision": 1}),
    Product("package", "Пакет", "Резюме, одностраничник и письмо под одну вакансию", 59, {"package": 1}),
    Product("week", "Неделя", "6 пакетов и 15 правок на 7 дней", 249, {"package": 6, "revision": 15}, 7),
    Product("month", "Месяц", "20 пакетов и 40 правок на 30 дней", 690, {"package": 20, "revision": 40}, 30),
]}
FREE = {"package": 1, "revision": 1}  # бесплатный старт: первая генерация и одна правка
PROMO_ONLY: dict[str, Product] = {  # только по промокоду: для своих и тестов
    "vip": Product("vip", "Безлимит на год", "Для своих", 0, {"package": 1000, "revision": 3000}, 365),
}


def product(sku: str) -> Product:
    p = PRODUCTS.get(sku) or PROMO_ONLY.get(sku)
    if p is None:
        raise BillingError(f"Нет такого продукта: {sku}")
    return p


class BillingError(ValueError):
    """Понятная пользователю причина."""


class PaymentRequired(BillingError):
    def __init__(self, action: str):
        self.action = action
        super().__init__(f"Бесплатный лимит закончился. Чтобы {ACTION_VERBS.get(action, action)}, "
                         f"выберите тариф или введите промокод.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d and d.tzinfo is None else d  # SQLite отдаёт без зоны


def is_admin(user: User) -> bool:
    return bool(user.tg_id) and user.tg_id in get_settings().admin_tg_ids


def enabled() -> bool:
    return get_settings().billing_enabled


async def _ensure_free(session: AsyncSession, user: User) -> None:
    has = (await session.execute(select(Grant.id).where(Grant.user_id == user.id, Grant.source == "free")
                                 .limit(1))).first()
    if not has:
        session.add(Grant(user_id=user.id, sku="free", quota=dict(FREE), source="free"))
        await session.flush()


async def _grants_left(session: AsyncSession, user: User) -> list[tuple[Grant, dict]]:
    """Действующие гранты с остатками; порядок списания: ближайший срок первым, бессрочные в конце."""
    await _ensure_free(session, user)
    now = _now()
    grants = (await session.execute(select(Grant).where(Grant.user_id == user.id))).scalars().all()
    grants = [g for g in grants if g.valid_to is None or _aware(g.valid_to) > now]
    if not grants:
        return []
    used = dict(((gid, act), n) for gid, act, n in (await session.execute(
        select(BillingUsage.grant_id, BillingUsage.action, func.count())
        .where(BillingUsage.grant_id.in_([g.id for g in grants]))
        .group_by(BillingUsage.grant_id, BillingUsage.action))).all())
    out = []
    for g in sorted(grants, key=lambda g: (g.valid_to is None, _aware(g.valid_to) or now, g.id)):
        left = {a: max(0, int(q) - used.get((g.id, a), 0)) for a, q in (g.quota or {}).items()}
        out.append((g, left))
    return out


async def require(session: AsyncSession, user: User, action: str | None) -> Grant | None:
    """Грант, из которого спишется действие. None: списывать не нужно (бесплатно, админ, биллинг выключен).
    PaymentRequired: лимита нет, каналу пора показать оплату."""
    if action is None or not enabled() or is_admin(user):
        return None
    if action not in ACTIONS:
        raise ValueError(f"unknown billing action {action}")
    for g, left in await _grants_left(session, user):
        if left.get(action, 0) > 0:
            return g
    raise PaymentRequired(action)


async def can(session: AsyncSession, user: User, action: str) -> bool:
    try:
        await require(session, user, action)
        return True
    except PaymentRequired:
        return False


async def consume(session: AsyncSession, user: User, action: str | None, *, target_id: int | None = None,
                  package_id: int | None = None) -> None:
    """Списать действие. Вызывать после успешной сборки в той же транзакции: при ошибке откатится вместе с ней."""
    if action is None or not enabled():
        return
    grant = await require(session, user, action)
    session.add(BillingUsage(user_id=user.id, grant_id=grant.id if grant else None, action=action,
                             target_id=target_id, package_id=package_id))
    await session.flush()


async def grant(session: AsyncSession, user: User, sku: str, source: str, ref: str | None = None) -> Grant:
    p = product(sku)
    g = Grant(user_id=user.id, sku=p.sku, quota=dict(p.quota), source=source, ref=ref,
              valid_to=_now() + timedelta(days=p.days) if p.days else None)
    session.add(g)
    await session.flush()
    return g


async def summary(session: AsyncSession, user: User) -> dict:
    """Для экранов: остатки по действиям, действующий тариф, каталог с ценами."""
    s = get_settings()
    totals = {a: 0 for a in ACTIONS}
    plan = None
    for g, left in await _grants_left(session, user):
        for a in ACTIONS:
            totals[a] += left.get(a, 0)
        if g.valid_to and plan is None and any(left.values()):
            plan = {"sku": g.sku, "title": product(g.sku).title, "until": _aware(g.valid_to).isoformat(), "left": left}
    return {
        "enabled": enabled(), "admin": is_admin(user), "left": totals, "plan": plan,
        "products": [{"sku": p.sku, "title": p.title, "desc": p.desc, "price_rub": p.price_rub,
                      "price_stars": p.price_stars, "days": p.days} for p in PRODUCTS.values()],
        "pay": {"yookassa": bool(s.yookassa_shop_id and s.yookassa_secret_key),
                "stars": s.stars_enabled and bool(s.telegram_bot_token)},
    }


def left_text(info: dict) -> str:
    """Короткая строка об остатках для бота и подсказок."""
    if info["admin"] or not info["enabled"]:
        return "Без ограничений"
    left = info["left"]
    txt = f"Осталось: пакетов {left['package']}, правок {left['revision']}"
    if info["plan"]:
        until = datetime.fromisoformat(info["plan"]["until"]).strftime("%d.%m")
        txt += f" (тариф «{info['plan']['title']}» до {until})"
    return txt


# ---------- Промокоды ----------

def _norm_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum() or ch in "-_")[:32]


async def create_promo(session: AsyncSession, sku: str, *, uses: int = 1, days: int | None = 30,
                       code: str | None = None, note: str | None = None, created_by: int | None = None) -> PromoCode:
    product(sku)  # проверка, что продукт существует
    code = _norm_code(code) if code else "JP-" + secrets.token_hex(3).upper()
    if not code:
        raise BillingError("Пустой промокод")
    if (await session.execute(select(PromoCode.id).where(PromoCode.code == code))).first():
        raise BillingError(f"Промокод {code} уже есть")
    pc = PromoCode(code=code, sku=sku, max_uses=max(1, uses), note=note, created_by=created_by,
                   expires_at=_now() + timedelta(days=days) if days else None)
    session.add(pc)
    await session.flush()
    return pc


async def redeem(session: AsyncSession, user: User, code: str) -> Grant:
    pc = (await session.execute(select(PromoCode).where(PromoCode.code == _norm_code(code)))).scalar_one_or_none()
    if pc is None:
        raise BillingError("Такого промокода нет")
    if pc.expires_at and _aware(pc.expires_at) < _now():
        raise BillingError("Срок промокода истёк")
    if pc.used >= pc.max_uses:
        raise BillingError("Промокод уже использован")
    if (await session.execute(select(PromoRedemption.id).where(PromoRedemption.code_id == pc.id,
                                                               PromoRedemption.user_id == user.id))).first():
        raise BillingError("Вы уже активировали этот промокод")
    pc.used += 1
    session.add(PromoRedemption(code_id=pc.id, user_id=user.id))
    return await grant(session, user, pc.sku, "promo", ref=pc.code)


async def list_promos(session: AsyncSession, limit: int = 50) -> list[dict]:
    rows = (await session.execute(select(PromoCode).order_by(PromoCode.id.desc()).limit(limit))).scalars()
    return [{"code": p.code, "sku": p.sku, "title": product(p.sku).title, "used": p.used, "max_uses": p.max_uses,
             "expires_at": _aware(p.expires_at).isoformat() if p.expires_at else None, "note": p.note} for p in rows]


# ---------- Платежи (провайдеры в services/payments.py) ----------

async def new_payment(session: AsyncSession, user: User, sku: str, provider: str) -> Payment:
    p = PRODUCTS.get(sku)
    if p is None:
        raise BillingError("Этот продукт не продаётся")
    if provider == "stars":
        amount, currency = p.price_stars, "XTR"
    elif provider == "yookassa":
        amount, currency = p.price_rub, "RUB"
    else:
        raise BillingError("Неизвестный способ оплаты")
    pay = Payment(user_id=user.id, provider=provider, sku=sku, amount=amount, currency=currency)
    session.add(pay)
    await session.flush()
    return pay


async def complete_payment(session: AsyncSession, payment_id: int, *, amount: int, currency: str,
                           external_id: str | None) -> Grant | None:
    """Оплата прошла: выдать грант. Идемпотентно: повторное событие ничего не начисляет (None)."""
    pay = await session.get(Payment, payment_id, with_for_update=True)
    if pay is None:
        raise BillingError("Платёж не найден")
    if pay.status == "succeeded":
        return None
    if amount != pay.amount or currency != pay.currency:
        raise BillingError(f"Сумма платежа {payment_id} не сходится")
    user = await session.get(User, pay.user_id)
    g = await grant(session, user, pay.sku, "payment", ref=f"pay:{pay.id}")
    pay.status, pay.paid_at, pay.grant_id = "succeeded", _now(), g.id
    pay.external_id = external_id or pay.external_id
    await session.flush()
    return g
