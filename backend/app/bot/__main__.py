"""Telegram-бот. Запуск: python -m app.bot

Бот делает всё основное сам (файлы, голос, профиль, поиск, пакеты), а для сложных экранов
открывает то же веб-приложение как Mini App (нужен https PUBLIC_URL).
"""

import asyncio
import html
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, Message, WebAppInfo)
from sqlalchemy import select

from app.auth import upsert_tg_user
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.jobs import build_profile_job, package_job, search_job
from app.models import Company, CompanyScore, Contact, Package, User, Vacancy, VacancyScore
from app.services import export, profile
from app.services.llm import LLMError
from app.services.stt import STTError, transcribe_ogg

log = logging.getLogger("bot")
dp = Dispatcher()
MAX_FILE = 15 * 1024 * 1024

CONSENT_TEXT = (
    "Привет! Я помогу найти работу: соберу ваш профиль из резюме и голосовых, найду подходящие вакансии "
    "и подготовлю CV и сопроводительное письмо под каждую.\n\n"
    "Для этого мне нужно обрабатывать ваши персональные данные: резюме, контакты, голосовые сообщения. "
    "Данные хранятся в России. Во внешнюю ИИ-модель уходит только обезличенный текст, без имени и контактов.\n\n"
    "Согласны на обработку персональных данных?"
)
HELP = (
    "<b>Как пользоваться</b>\n"
    "1. Пришлите резюме (PDF, DOCX, TXT), отзывы, описание проектов. Можно голосовыми до 30 секунд или текстом.\n"
    "2. /profile соберёт профиль и задаст уточняющие вопросы. Отвечайте просто сообщением.\n"
    "3. /search найдёт и оценит вакансии.\n"
    "4. /vacancies покажет лучшие, по кнопке соберу CV и письмо под вакансию.\n"
    "/companies подходящие компании · /contacts ваши ФИО и контакты для CV"
)


def web_url(path: str = "") -> str | None:
    url = get_settings().public_url
    return f"{url.rstrip('/')}/{path.lstrip('/')}" if url.startswith("https://") else None


def webapp_kb(path: str, text: str) -> InlineKeyboardMarkup | None:
    url = web_url(path)
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]]) if url else None


async def get_user(msg_from) -> User:
    async with SessionLocal() as s:
        u = await upsert_tg_user(s, msg_from.id, msg_from.username, msg_from.first_name)
        await s.commit()
        return u


async def need_consent(message: Message, user: User) -> bool:
    if user.consent_at:
        return False
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Согласен", callback_data="consent")]])
    await message.answer(CONSENT_TEXT, reply_markup=kb)
    return True


async def run_job(fn) -> dict:
    """В боте задачи выполняем сразу и отвечаем по готовности."""
    async with SessionLocal() as s:
        try:
            res = await fn(s)
            await s.commit()
            return res
        except Exception:
            await s.rollback()
            raise


async def report_error(message: Message, e: Exception):
    if isinstance(e, (ValueError, LLMError, STTError)):
        await message.answer(f"⚠️ {html.escape(str(e))}")
    else:
        log.exception("bot error")
        await message.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.")


# ---------- Старт и согласие ----------

@dp.message(CommandStart())
async def start(message: Message):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    await message.answer(HELP, reply_markup=webapp_kb("", "Открыть приложение"))


@dp.callback_query(F.data == "consent")
async def on_consent(cb: CallbackQuery):
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.tg_id == cb.from_user.id))).scalar_one()
        u.consent_at = datetime.now(timezone.utc)
        await s.commit()
    await cb.message.edit_text("Спасибо, согласие получено.")
    await cb.message.answer(HELP, reply_markup=webapp_kb("", "Открыть приложение"))
    await cb.answer()


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(HELP)


@dp.message(Command("contacts"))
async def contacts_cmd(message: Message):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        vals = [x.strip() or None for x in parts[1].split(";")] + [None] * 4
        async with SessionLocal() as s:
            c = await profile.get_contact(s, await s.get(User, user.id))
            c.full_name, c.email, c.phone, c.city = vals[:4]
            await s.commit()
        await message.answer("Контакты сохранены.")
        return
    async with SessionLocal() as s:
        c = await s.get(Contact, user.id)
    cur = (f"Сейчас: {html.escape(c.full_name or '—')}, {html.escape(c.email or '—')}, "
           f"{html.escape(c.phone or '—')}, {html.escape(c.city or '—')}") if c else "Контакты пока не заданы."
    await message.answer(
        f"{cur}\n\nЧтобы изменить, отправьте одной строкой через «;»:\n"
        "<code>/contacts ФИО; email; телефон; город</code>\n"
        "Эти данные попадают только в итоговые файлы CV и не отправляются в ИИ.")


# ---------- Материалы ----------

@dp.message(F.document)
async def on_document(message: Message, bot: Bot):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    doc = message.document
    if doc.file_size and doc.file_size > MAX_FILE:
        await message.answer("Файл больше 15 МБ, пришлите поменьше.")
        return
    buf = await bot.download(doc)
    async with SessionLocal() as s:
        src = await profile.add_file_source(s, await s.get(User, user.id), doc.file_name or "file", buf.read())
        await s.commit()
    if src.status == "error":
        await message.answer(f"⚠️ {html.escape(src.error or 'Не удалось прочитать файл')}")
    else:
        await message.answer(f"📄 Принял «{html.escape(doc.file_name or 'файл')}» ({len(src.text or '')} символов). "
                             "Пришлите ещё материалы или соберите профиль: /profile")


@dp.message(F.voice)
async def on_voice(message: Message, bot: Bot):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    buf = await bot.download(message.voice)
    try:
        text = await transcribe_ogg(buf.read(), message.voice.duration)
    except STTError as e:
        await message.answer(f"⚠️ {html.escape(str(e))}")
        return
    if not text.strip():
        await message.answer("Не расслышал, попробуйте ещё раз.")
        return
    await _save_text(message, user, text, kind="voice")


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    await _save_text(message, user, message.text)


async def _save_text(message: Message, user: User, text: str, kind: str = "text"):
    async with SessionLocal() as s:
        u = await s.get(User, user.id)
        src = await profile.add_text_source(s, u, text, kind=kind)
        has_profile = await profile.latest_profile(s, user.id) is not None
        await s.commit()
    head = f"🎙 Распознал: «{html.escape(text[:300])}»\n\n" if kind == "voice" else ""
    if has_profile:
        await message.answer(head + "Обновляю профиль с учётом этого…")
        try:
            await run_job(build_profile_job(user.id, [src.id]))
            await _send_profile(message, user.id)
        except Exception as e:
            await report_error(message, e)
    else:
        await message.answer(head + "Записал. Когда материалов хватит, соберите профиль: /profile")


# ---------- Профиль ----------

async def _send_profile(message: Message, user_id: int):
    async with SessionLocal() as s:
        p = await profile.latest_profile(s, user_id)
    if p is None:
        return
    d = p.data
    skills = ", ".join(sk["name"] for sk in d.get("skills", [])[:15])
    ex = d.get("expectations", {})
    text = (f"<b>{html.escape(d.get('headline') or 'Профиль')}</b> · v{p.version}\n"
            f"{html.escape(d.get('summary') or '')}\n\n"
            f"<b>Роли:</b> {html.escape(', '.join(d.get('target_roles', [])) or '—')}\n"
            f"<b>Уровень:</b> {html.escape(d.get('seniority') or '—')}\n"
            f"<b>Навыки:</b> {html.escape(skills or '—')}\n"
            f"<b>Ожидания:</b> от {ex.get('salary_min') or '?'} {ex.get('currency', 'RUB')}, "
            f"{html.escape(', '.join(ex.get('cities', [])) or 'город ?')}, "
            f"{html.escape(', '.join(ex.get('formats', [])) or 'формат ?')}")
    if p.questions:
        text += "\n\n<b>Уточните, пожалуйста</b> (ответьте сообщением или голосом):\n" + "\n".join(
            f"• {html.escape(q['question'])}" for q in p.questions)
    await message.answer(text, reply_markup=webapp_kb("profile", "Редактировать профиль"))


@dp.message(Command("profile"))
async def profile_cmd(message: Message):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    async with SessionLocal() as s:
        exists = await profile.latest_profile(s, user.id) is not None
    if exists and "rebuild" not in (message.text or ""):
        await _send_profile(message, user.id)
        await message.answer("Пересобрать с нуля по всем материалам: /profile rebuild")
        return
    await message.answer("Собираю профиль, это займёт до минуты…")
    try:
        await run_job(build_profile_job(user.id))
        await _send_profile(message, user.id)
    except Exception as e:
        await report_error(message, e)


# ---------- Поиск ----------

@dp.message(Command("search"))
async def search_cmd(message: Message):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    await message.answer("Ищу и оцениваю вакансии. Это может занять несколько минут…")
    try:
        res = await run_job(search_job(user.id))
    except Exception as e:
        await report_error(message, e)
        return
    c, sc = res["collect"], res["score"]
    await message.answer(f"Готово. Новых вакансий: {c['created']}, оценено: {sc['scored']}, "
                         f"отсеяно фильтрами: {sc['filtered']}.")
    await vacancies_cmd(message)


@dp.message(Command("vacancies"))
async def vacancies_cmd(message: Message):
    user = await get_user(message.from_user)
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(VacancyScore, Vacancy).join(Vacancy, Vacancy.id == VacancyScore.vacancy_id)
            .where(VacancyScore.user_id == user.id, VacancyScore.score.is_not(None))
            .order_by(VacancyScore.score.desc()).limit(10))).all()
    if not rows:
        await message.answer("Пока нет оценённых вакансий. Запустите /search")
        return
    lines, kb = ["<b>Лучшие вакансии</b>"], []
    seen = set()
    for i, (sc, v) in enumerate(rows, 1):
        if v.id in seen:
            continue
        seen.add(v.id)
        salary = f" · от {v.salary_from:,}".replace(",", " ") if v.salary_from else ""
        lines.append(f"\n{i}. <b>{sc.score}%</b> <a href=\"{html.escape(v.url)}\">{html.escape(v.title)}</a>\n"
                     f"{html.escape(v.company_name)} · {html.escape(v.city or '')}{salary}\n"
                     f"✅ {html.escape('; '.join(sc.pros[:2]))}\n⚠️ {html.escape('; '.join(sc.gaps[:2]))}")
        kb.append([InlineKeyboardButton(text=f"{i}. CV и письмо", callback_data=f"pkg:{v.id}")])
    if url := web_url("vacancies"):
        kb.append([InlineKeyboardButton(text="Все вакансии", web_app=WebAppInfo(url=url))])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
                         disable_web_page_preview=True)


@dp.message(Command("companies"))
async def companies_cmd(message: Message):
    user = await get_user(message.from_user)
    async with SessionLocal() as s:
        p = await profile.latest_profile(s, user.id)
        if p is None:
            await message.answer("Сначала соберите профиль: /profile")
            return
        rows = (await s.execute(select(CompanyScore, Company).join(Company, Company.id == CompanyScore.company_id)
                                .where(CompanyScore.user_id == user.id, CompanyScore.profile_version == p.version)
                                .order_by(CompanyScore.score.desc()).limit(10))).all()
    if not rows:
        await message.answer("Компании ещё не оценены. Запустите /search")
        return
    lines = ["<b>Подходящие компании</b>"]
    for sc, c in rows:
        link = f" · <a href=\"{html.escape(c.careers_url)}\">вакансии</a>" if c.careers_url else ""
        lines.append(f"\n<b>{sc.score}%</b> {html.escape(c.name)}{link}\n{html.escape('; '.join(sc.reasons[:2]))}")
    await message.answer("\n".join(lines), disable_web_page_preview=True,
                         reply_markup=webapp_kb("companies", "Весь каталог"))


# ---------- Пакет ----------

@dp.callback_query(F.data.startswith("pkg:"))
async def on_package(cb: CallbackQuery):
    vacancy_id = int(cb.data.split(":")[1])
    await cb.answer("Готовлю пакет…")
    await cb.message.answer("Собираю досье компании и пишу CV с письмом. Обычно 1-2 минуты…")
    try:
        user = await get_user(cb.from_user)
        res = await run_job(package_job(user.id, vacancy_id, None))
    except Exception as e:
        await report_error(cb.message, e)
        return
    await send_package(cb.message, res["package_id"])


async def send_package(message: Message, package_id: int):
    async with SessionLocal() as s:
        pkg = await s.get(Package, package_id)
        contact = await s.get(Contact, pkg.user_id)
        vacancy = await s.get(Vacancy, pkg.vacancy_id)
    view = export.cv_view(pkg, contact, drop_flagged=False)
    await message.answer(f"<b>Сопроводительное письмо</b> · {html.escape(vacancy.title)}\n\n"
                         f"{html.escape(view['cover_letter'])}")
    await message.answer(f"<b>Сообщение рекрутеру</b>\n\n<code>{html.escape(view['recruiter_message'])}</code>")
    if pkg.fact_flags:
        flags = "\n".join(f"• {html.escape(f['text'])} — {html.escape(f['reason'])}" for f in pkg.fact_flags)
        await message.answer(f"⚠️ <b>Не подтверждается профилем:</b>\n{flags}\n\n"
                             "Пункты CV с этими утверждениями в файл не попадут. Если это правда, "
                             "добавьте факт в профиль и пересоберите пакет.")
    letter_flags = any(f.get("where") != "cv" for f in pkg.fact_flags or [])
    if not letter_flags:
        name = f"CV_{pkg.vacancy_id}_v{pkg.version}"
        await message.answer_document(BufferedInputFile(export.to_docx(pkg, contact), f"{name}.docx"))
        await message.answer_document(BufferedInputFile(export.to_pdf(pkg, contact), f"{name}.pdf"))
    kb = [[InlineKeyboardButton(text="Пересобрать", callback_data=f"pkg:{pkg.vacancy_id}")]]
    if url := web_url(f"vacancies/{pkg.vacancy_id}"):
        kb.append([InlineKeyboardButton(text="Править в приложении", web_app=WebAppInfo(url=url))])
    await message.answer(f"Откликнуться: {html.escape(vacancy.url)}", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
                         disable_web_page_preview=True)


async def main():
    s = get_settings()
    if not s.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в .env")
    await init_db()
    bot = Bot(s.telegram_bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    if url := web_url():
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=url)))
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
