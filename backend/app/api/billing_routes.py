"""HTTP API биллинга: тариф и остатки, оплата (ЮKassa, Stars), промокоды, админка промокодов и расходов."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user
from app.config import get_settings
from app.db import get_session
from app.models import BillingUsage, LlmUsage, Payment, User
from app.services import billing, payments

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def payment_required(e: billing.PaymentRequired) -> HTTPException:
    """402 с машинным полем need: PWA по нему показывает экран оплаты."""
    return HTTPException(402, {"message": str(e), "need": e.action})


@router.get("/billing/public")
async def billing_public():
    """Без входа: цены для лендинга и реквизиты для оферты. Один источник с ботом и оплатой."""
    s = get_settings()
    return {"products": [{"sku": p.sku, "title": p.title, "desc": p.desc, "price_rub": p.price_rub,
                          "price_stars": p.price_stars, "days": p.days} for p in billing.PRODUCTS.values()],
            "free": billing.FREE, "bot_username": s.telegram_bot_username, "site": s.public_url,
            "legal": {"name": s.legal_name, "inn": s.legal_inn, "status": s.legal_status, "email": s.support_email}}


@router.get("/billing")
async def billing_info(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    info = await billing.summary(session, user)
    await session.commit()  # мог создаться бесплатный грант
    return info


class CheckoutIn(BaseModel):
    sku: str
    provider: str  # yookassa | stars


@router.post("/billing/checkout")
async def checkout(body: CheckoutIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    s = get_settings()
    try:
        if body.provider == "stars" and not (s.stars_enabled and s.telegram_bot_token):
            raise billing.BillingError("Оплата звёздами выключена. Можно ввести промокод.")
        if body.provider == "yookassa" and not payments.yookassa_on():
            raise billing.BillingError("Оплата картой пока не подключена. Можно ввести промокод.")
        pay = await billing.new_payment(session, user, body.sku, body.provider)
        if body.provider == "stars":
            out = {"payment_id": pay.id, "invoice_link": await payments.stars_invoice_link(pay)}
        else:
            out = {"payment_id": pay.id, "url": await payments.yookassa_create(pay)}
    except billing.BillingError as e:
        raise HTTPException(400, str(e))
    await session.commit()
    return out


@router.get("/billing/payments/{payment_id}")
async def payment_status(payment_id: int, user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    pay = await session.get(Payment, payment_id)
    if pay is None or pay.user_id != user.id:
        raise HTTPException(404)
    if pay.provider == "yookassa":
        await payments.yookassa_sync(session, pay)
        await session.commit()
    return {"id": pay.id, "status": pay.status, "sku": pay.sku, "title": billing.product(pay.sku).title}


@router.post("/billing/yookassa/webhook")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    """Уведомление ЮKassa. Телу не доверяем: по id платежа перепроверяем статус запросом в API."""
    try:
        data = await request.json()
        ext_id = str(data["object"]["id"])
    except Exception:
        raise HTTPException(400)
    pay = (await session.execute(select(Payment).where(Payment.external_id == ext_id,
                                                       Payment.provider == "yookassa"))).scalar_one_or_none()
    if pay is None:
        log.warning("yookassa webhook: unknown payment")
        return {"ok": True}  # 200, иначе ЮKassa будет повторять
    try:
        await payments.yookassa_sync(session, pay)
    except billing.BillingError as e:
        log.error("yookassa webhook payment=%s: %s", pay.id, e)
    await session.commit()
    return {"ok": True}


class PromoIn(BaseModel):
    code: str


@router.post("/billing/promo")
async def redeem_promo(body: PromoIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    try:
        g = await billing.redeem(session, user, body.code)
    except billing.BillingError as e:
        raise HTTPException(400, str(e))
    await session.commit()
    return {"ok": True, "title": billing.product(g.sku).title, "billing": await billing.summary(session, user)}


# ---------- Администрирование ----------

def _admin(user: User = Depends(current_user)) -> User:
    if not billing.is_admin(user) and get_settings().env != "dev":
        raise HTTPException(403)
    return user


class NewPromoIn(BaseModel):
    sku: str
    uses: int = 1
    days: int | None = 30  # сколько дней код действует
    code: str | None = None
    note: str | None = None


@router.post("/admin/promo")
async def admin_new_promo(body: NewPromoIn, user: User = Depends(_admin), session: AsyncSession = Depends(get_session)):
    try:
        pc = await billing.create_promo(session, body.sku, uses=body.uses, days=body.days, code=body.code,
                                        note=body.note, created_by=user.id)
    except billing.BillingError as e:
        raise HTTPException(400, str(e))
    await session.commit()
    return {"code": pc.code, "sku": pc.sku, "max_uses": pc.max_uses}


@router.get("/admin/promo")
async def admin_promos(_: User = Depends(_admin), session: AsyncSession = Depends(get_session)):
    return {"promos": await billing.list_promos(session),
            "skus": [{"sku": p.sku, "title": p.title} for p in [*billing.PRODUCTS.values(), *billing.PROMO_ONLY.values()]]}


@router.get("/admin/usage")
async def admin_usage(days: int = 30, _: User = Depends(_admin), session: AsyncSession = Depends(get_session)):
    """Расходы на модель: по шагам, по действиям (средняя цена одного действия) и платные действия пользователей."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    steps = (await session.execute(
        select(LlmUsage.step, LlmUsage.model, func.count(), func.sum(LlmUsage.prompt_tokens),
               func.sum(LlmUsage.completion_tokens), func.sum(LlmUsage.cost_usd))
        .where(LlmUsage.created_at >= since).group_by(LlmUsage.step, LlmUsage.model))).all()
    runs = (await session.execute(
        select(LlmUsage.action, LlmUsage.run, func.sum(LlmUsage.cost_usd))
        .where(LlmUsage.created_at >= since).group_by(LlmUsage.action, LlmUsage.run))).all()
    by_action: dict[str, list[float]] = {}
    for action, _run, cost in runs:
        by_action.setdefault(action or "other", []).append(float(cost or 0))
    paid = (await session.execute(select(BillingUsage.action, func.count()).where(BillingUsage.created_at >= since)
                                  .group_by(BillingUsage.action))).all()
    total = sum(float(r[5] or 0) for r in steps)
    return {
        "days": days, "total_usd": round(total, 4),
        "steps": [{"step": r[0], "model": r[1], "calls": r[2], "prompt_tokens": int(r[3] or 0),
                   "completion_tokens": int(r[4] or 0), "cost_usd": round(float(r[5] or 0), 4)} for r in steps],
        "actions": [{"action": a, "runs": len(v), "avg_usd": round(sum(v) / len(v), 4), "max_usd": round(max(v), 4)}
                    for a, v in sorted(by_action.items())],
        "billed": {a: n for a, n in paid},
    }
