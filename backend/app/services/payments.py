"""Провайдеры оплаты. Правила и выдача грантов в services/billing.py, здесь только связь с провайдером.

- ЮKassa: сайт и PWA в браузере. Статус всегда перепроверяется запросом в API, телу вебхука не доверяем.
- Telegram Stars: бот и Mini App (цифровые товары внутри Telegram продаются только за звёзды).
  Счёт создаёт бот; payload это id платежа; грант выдаёт обработчик successful_payment (bot/billing_flow.py).
"""

import logging
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Payment
from app.services import billing

log = logging.getLogger(__name__)
YOOKASSA_API = "https://api.yookassa.ru/v3"
STARS_PAYLOAD = "jp-pay:"


class PaymentError(billing.BillingError):
    pass


def yookassa_on() -> bool:
    s = get_settings()
    return bool(s.yookassa_shop_id and s.yookassa_secret_key)


def _yk() -> httpx.AsyncClient:  # ЮKassa российская: без прокси
    s = get_settings()
    return httpx.AsyncClient(base_url=YOOKASSA_API, auth=(s.yookassa_shop_id, s.yookassa_secret_key),
                             timeout=httpx.Timeout(30, connect=10))


async def yookassa_create(pay: Payment) -> str:
    """Создать платёж в ЮKassa, вернуть ссылку на оплату."""
    if not yookassa_on():
        raise PaymentError("Оплата картой пока не подключена. Можно ввести промокод.")
    p = billing.product(pay.sku)
    base = get_settings().public_url.rstrip("/")
    body = {"amount": {"value": f"{pay.amount}.00", "currency": "RUB"}, "capture": True,
            "confirmation": {"type": "redirect", "return_url": f"{base}/pay/return?pid={pay.id}"},
            "description": f"JobPilot: {p.title}. {p.desc}"[:128], "metadata": {"payment_id": str(pay.id)}}
    async with _yk() as c:
        r = await c.post("/payments", json=body, headers={"Idempotence-Key": f"jp-{pay.id}-{uuid.uuid4().hex[:8]}"})
    if r.status_code >= 400:
        log.error("yookassa create %s: %s", r.status_code, r.text[:300])
        raise PaymentError("ЮKassa не приняла платёж. Попробуйте позже.")
    data = r.json()
    pay.external_id = data["id"]
    return data["confirmation"]["confirmation_url"]


async def yookassa_sync(session: AsyncSession, pay: Payment) -> str:
    """Перепроверить статус в ЮKassa и выдать грант, если оплачено. Возвращает статус нашего платежа."""
    if pay.status != "pending" or not pay.external_id or not yookassa_on():
        return pay.status
    async with _yk() as c:
        r = await c.get(f"/payments/{pay.external_id}")
    if r.status_code >= 400:
        log.warning("yookassa get %s: %s", r.status_code, r.text[:200])
        return pay.status
    data = r.json()
    if data.get("status") == "succeeded" and data.get("paid"):
        value = data["amount"]["value"]
        await billing.complete_payment(session, pay.id, amount=int(float(value)), currency=data["amount"]["currency"],
                                       external_id=data["id"])
    elif data.get("status") == "canceled":
        pay.status = "canceled"
    return pay.status


async def stars_invoice_link(pay: Payment) -> str:
    """Ссылка на счёт в звёздах для Mini App (Telegram.WebApp.openInvoice)."""
    from aiogram.types import LabeledPrice

    from app.tg import make_bot

    p = billing.product(pay.sku)
    bot = make_bot()
    try:
        return await bot.create_invoice_link(title=f"JobPilot: {p.title}", description=p.desc,
                                             payload=f"{STARS_PAYLOAD}{pay.id}", currency="XTR",
                                             prices=[LabeledPrice(label=p.title, amount=pay.amount)])
    except Exception as e:
        log.exception("stars invoice link")
        raise PaymentError("Telegram не создал счёт. Попробуйте ещё раз.") from e
    finally:
        await bot.session.close()


def stars_payment_id(payload: str) -> int | None:
    if payload and payload.startswith(STARS_PAYLOAD):
        try:
            return int(payload[len(STARS_PAYLOAD):])
        except ValueError:
            return None
    return None
