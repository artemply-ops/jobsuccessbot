"""Бот: тариф и остатки, промокод, оплата звёздами, выпуск промокодов администратором.

Цифровые товары внутри Telegram продаются только за Stars, поэтому в боте нет ссылки на оплату картой.
Правила и цены в services/billing.py, здесь только диалог.
"""

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery

from app.config import get_settings
from app.db import SessionLocal
from app.models import Payment, User
from app.services import billing, payments

log = logging.getLogger("bot")
router = Router()


class Promo(StatesGroup):
    code = State()


async def _user(tg) -> User:
    from app.auth import upsert_tg_user

    async with SessionLocal() as s:
        u = await upsert_tg_user(s, tg.id, tg.username, tg.first_name)
        await s.commit()
        return u


def plans_kb(need: str | None = None) -> InlineKeyboardMarkup:
    """Кнопки покупки. need: что закончилось; разовый продукт под него ставим первым."""
    rows = []
    if get_settings().stars_enabled:
        order = list(billing.PRODUCTS.values())
        if need in billing.PRODUCTS:
            order.sort(key=lambda p: p.sku != need)
        for p in order:
            rows.append([InlineKeyboardButton(text=f"{p.title} · {p.price_stars} ⭐  ({p.desc.lower()})"[:64],
                                              callback_data=f"buy:{p.sku}")])
    rows.append([InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="promo:ask")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prices_text() -> str:
    stars = get_settings().stars_enabled
    lines = ["<b>Цены</b>"]
    for p in billing.PRODUCTS.values():
        price = f"{p.price_stars} ⭐ (на сайте {p.price_rub} ₽)" if stars else f"{p.price_rub} ₽"
        lines.append(f"• {p.title}: {price}. {html.escape(p.desc)}")
    if not stars:
        lines.append("\nОплата в Telegram скоро появится. Пока можно активировать промокод: /promo")
    return "\n".join(lines)


async def show_paywall(message: Message, user: User, need: str) -> None:
    """Лимит закончился: остатки, цены и кнопки. Вызывают cv_flow перед генерацией и правкой."""
    async with SessionLocal() as s:
        info = await billing.summary(s, user)
        await s.commit()
    await message.answer(f"💳 {html.escape(str(billing.PaymentRequired(need)))}\n\n{billing.left_text(info)}\n\n"
                         f"{prices_text()}", reply_markup=plans_kb(need))


async def check_or_paywall(message: Message, user: User, action: str | None) -> bool:
    """True: можно продолжать. False: показали оплату."""
    async with SessionLocal() as s:
        try:
            await billing.require(s, user, action)
            await s.commit()
            return True
        except billing.PaymentRequired:
            pass
    await show_paywall(message, user, action)
    return False


@router.message(Command("plan"))
async def plan_cmd(message: Message):
    user = await _user(message.from_user)
    async with SessionLocal() as s:
        info = await billing.summary(s, user)
        await s.commit()
    await message.answer(f"<b>Ваш тариф</b>\n{billing.left_text(info)}\n\nБесплатно всегда: проверка резюме и "
                         f"покрытие требований.\n\n{prices_text()}", reply_markup=plans_kb())


@router.message(Command("promo"))
async def promo_cmd(message: Message, state: FSMContext):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        await redeem(message, parts[1])
        return
    await state.set_state(Promo.code)
    await message.answer("Пришлите промокод одним сообщением.")


@router.callback_query(F.data == "promo:ask")
async def promo_ask(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(Promo.code)
    await cb.message.answer("Пришлите промокод одним сообщением.")


async def promo_take(message: Message, state: FSMContext):
    """Текст в состоянии Promo.code: вызывает handlers.on_text (общий обработчик текста стоит раньше роутеров)."""
    await state.set_state(None)
    await redeem(message, message.text)


async def redeem(message: Message, code: str) -> None:
    user = await _user(message.from_user)
    async with SessionLocal() as s:
        try:
            g = await billing.redeem(s, user, code.strip())
            await s.commit()
            info = await billing.summary(s, user)
        except billing.BillingError as e:
            await message.answer(f"⚠️ {html.escape(str(e))}")
            return
    await message.answer(f"✅ Промокод активирован: «{billing.product(g.sku).title}».\n{billing.left_text(info)}\n\n"
                         "Продолжить: /new новая вакансия или кнопка правки под последним пакетом.")


# ---------- Оплата звёздами ----------

@router.callback_query(F.data.startswith("buy:"))
async def buy(cb: CallbackQuery):
    await cb.answer()
    sku = cb.data.split(":", 1)[1]
    if not get_settings().stars_enabled:
        await cb.message.answer("Оплата пока выключена. Можно ввести промокод: /promo")
        return
    user = await _user(cb.from_user)
    async with SessionLocal() as s:
        try:
            pay = await billing.new_payment(s, user, sku, "stars")
        except billing.BillingError as e:
            await cb.message.answer(f"⚠️ {html.escape(str(e))}")
            return
        await s.commit()
    p = billing.product(sku)
    await cb.message.answer_invoice(title=f"JobPilot: {p.title}", description=p.desc,
                                    payload=f"{payments.STARS_PAYLOAD}{pay.id}", currency="XTR",
                                    prices=[LabeledPrice(label=p.title, amount=pay.amount)])


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    """Telegram спрашивает, можно ли списать звёзды: проверяем, что платёж наш и ещё не оплачен."""
    pid = payments.stars_payment_id(q.invoice_payload)
    async with SessionLocal() as s:
        pay = await s.get(Payment, pid) if pid else None
    ok = bool(pay and pay.status == "pending" and pay.amount == q.total_amount and q.currency == "XTR")
    await q.answer(ok=ok, error_message=None if ok else "Счёт устарел. Откройте /plan и попробуйте снова.")


@router.message(F.successful_payment)
async def paid(message: Message):
    sp = message.successful_payment
    pid = payments.stars_payment_id(sp.invoice_payload)
    if pid is None:
        return
    async with SessionLocal() as s:
        try:
            g = await billing.complete_payment(s, pid, amount=sp.total_amount, currency=sp.currency,
                                               external_id=sp.telegram_payment_charge_id)
            await s.commit()
        except billing.BillingError as e:
            log.error("stars payment %s: %s", pid, e)
            await message.answer("⚠️ Оплата получена, но не применилась. Напишите в поддержку, мы всё исправим.")
            return
        user = await _user(message.from_user)
        info = await billing.summary(s, user)
    if g:
        await message.answer(f"✅ Спасибо! Подключено: «{billing.product(g.sku).title}».\n{billing.left_text(info)}\n\n"
                             "Продолжить: /new новая вакансия или кнопка правки под последним пакетом.")


# ---------- Администратор ----------

@router.message(Command("promo_new"))
async def promo_new(message: Message):
    """/promo_new <продукт> [активаций] [дней действия] [КОД]. Продукты: revision, package, week, month, vip."""
    user = await _user(message.from_user)
    if not billing.is_admin(user):
        return
    parts = (message.text or "").split()[1:]
    skus = ", ".join([*billing.PRODUCTS, *billing.PROMO_ONLY])
    if not parts:
        await message.answer(f"Формат: <code>/promo_new month 10 30</code> (продукт, активаций, дней действия кода, "
                             f"свой код по желанию).\nПродукты: {skus}")
        return
    try:
        sku = parts[0]
        uses = int(parts[1]) if len(parts) > 1 else 1
        days = int(parts[2]) if len(parts) > 2 else 30
        code = parts[3] if len(parts) > 3 else None
        async with SessionLocal() as s:
            pc = await billing.create_promo(s, sku, uses=uses, days=days or None, code=code, created_by=user.id)
            await s.commit()
    except (ValueError, billing.BillingError) as e:
        await message.answer(f"⚠️ {html.escape(str(e))}\nПродукты: {skus}")
        return
    await message.answer(f"🎟 Промокод <code>{pc.code}</code>: «{billing.product(pc.sku).title}», "
                         f"активаций {pc.max_uses}, действует {days} дн.\nАктивация: /promo {pc.code}")


@router.message(Command("promo_list"))
async def promo_list(message: Message):
    user = await _user(message.from_user)
    if not billing.is_admin(user):
        return
    async with SessionLocal() as s:
        rows = await billing.list_promos(s, 20)
    if not rows:
        await message.answer("Промокодов пока нет. Создать: /promo_new")
        return
    await message.answer("\n".join(f"<code>{r['code']}</code> · {r['title']} · {r['used']}/{r['max_uses']}" for r in rows))
