"""Хендлеры Telegram-бота.

Бот делает всё основное сам (файлы, голос, профиль, поиск, пакеты), а для сложных экранов
открывает то же веб-приложение как Mini App (нужен https PUBLIC_URL).
Пока ИИ не подключён, шаги с ИИ отвечают заглушкой и ведут пользователя к следующему шагу.
"""

import html
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, Message, WebAppInfo)
from sqlalchemy import func, select

from app.auth import upsert_tg_user
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.jobs import build_profile_job, package_job, search_job
from app.models import Company, CompanyScore, Contact, Package, Source, User, Vacancy, VacancyScore
from app.services import export, llm, profile, stt
from app.services.llm import LLMError
from app.services.stt import STTError, transcribe_ogg
from app.tg import make_bot

log = logging.getLogger("bot")
dp = Dispatcher()
MAX_FILE = 15 * 1024 * 1024


class Basic(StatesGroup):
    waiting = State()


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
STUB = "🤖 <i>{what} работает на ИИ, а ИИ пока не подключён.</i>"
BASIC_ASK = ("Чтобы уже сейчас искать вакансии, напишите одной строкой должность и город, можно ещё зарплату "
             "и формат. Например:\n<code>Python-разработчик, Москва, 250000, удалёнка</code>")
COMMANDS = [("start", "Начало"), ("profile", "Профиль"), ("search", "Найти вакансии"),
            ("vacancies", "Лучшие вакансии"), ("companies", "Компании"), ("contacts", "Контакты для CV"),
            ("help", "Помощь")]


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
async def start(message: Message, state: FSMContext):
    await state.clear()
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
                             "Пришлите ещё материалы или переходите к профилю: /profile")


@dp.message(F.voice)
async def on_voice(message: Message, bot: Bot):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    if not stt.available():
        await message.answer(f"🎙 <i>{stt.STUB}</i>")
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


@dp.message(Basic.waiting, F.text & ~F.text.startswith("/"))
async def on_basic(message: Message, state: FSMContext):
    user = await get_user(message.from_user)
    try:
        async with SessionLocal() as s:
            p = await profile.save_basic(s, await s.get(User, user.id), message.text)
            await s.commit()
    except ValueError as e:
        await message.answer(f"⚠️ {html.escape(str(e))}")
        return
    await state.clear()
    await _send_profile(message, user.id)
    await message.answer("Профиль сохранён. Следующий шаг: /search, найду вакансии.")


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
    if has_profile and llm.available():
        await message.answer(head + "Обновляю профиль с учётом этого…")
        try:
            await run_job(build_profile_job(user.id, [src.id]))
            await _send_profile(message, user.id)
        except Exception as e:
            await report_error(message, e)
    else:
        await message.answer(head + "Записал. Когда материалов хватит, переходите к профилю: /profile")


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
            + (f"{html.escape(d['summary'])}\n" if d.get("summary") else "") +
            f"\n<b>Роли:</b> {html.escape(', '.join(d.get('target_roles', [])) or '—')}\n"
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
async def profile_cmd(message: Message, state: FSMContext):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    async with SessionLocal() as s:
        exists = await profile.latest_profile(s, user.id) is not None
        n_sources = (await s.execute(select(func.count()).where(Source.user_id == user.id))).scalar()
    if not llm.available():
        if exists:
            await _send_profile(message, user.id)
        note = (f" Ваши материалы ({n_sources}) сохранены, профиль по ним соберу, как только подключим ИИ."
                if n_sources else "")
        await message.answer(STUB.format(what="Сборка профиля из резюме") + note + "\n\n" + BASIC_ASK)
        await state.set_state(Basic.waiting)
        return
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
async def search_cmd(message: Message, state: FSMContext):
    user = await get_user(message.from_user)
    if await need_consent(message, user):
        return
    async with SessionLocal() as s:
        has_profile = await profile.latest_profile(s, user.id) is not None
    if not has_profile:
        await message.answer("Сначала профиль. " + BASIC_ASK)
        await state.set_state(Basic.waiting)
        return
    await message.answer("Ищу вакансии в открытых источниках. Это займёт до пары минут…")
    try:
        res = await run_job(search_job(user.id))
    except Exception as e:
        await report_error(message, e)
        return
    c, sc = res["collect"], res["score"]
    text = (f"Готово. Новых вакансий: {c['created']}, обновлено: {c['updated']}, "
            f"оценено: {sc['scored']}, отсеяно фильтрами: {sc['filtered']}.")
    if sc.get("method") == "basic":
        text += "\n\n" + STUB.format(what="Точная оценка вакансий") + (
            " Пока показываю предварительную оценку по совпадению с должностью.")
    await message.answer(text)
    await vacancies_cmd(message)


@dp.message(Command("vacancies"))
async def vacancies_cmd(message: Message):
    user = await get_user(message.from_user)
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(VacancyScore, Vacancy).join(Vacancy, Vacancy.id == VacancyScore.vacancy_id)
            .where(VacancyScore.user_id == user.id, VacancyScore.score.is_not(None))
            .order_by(VacancyScore.profile_version.desc(), VacancyScore.score.desc()).limit(30))).all()
    if not rows:
        await message.answer("Пока нет подходящих вакансий. Запустите /search")
        return
    lines, kb, seen = ["<b>Лучшие вакансии</b>"], [], set()
    for sc, v in rows:
        if v.id in seen or len(seen) >= 10:
            continue
        seen.add(v.id)
        i = len(seen)
        salary = f" · от {v.salary_from:,}".replace(",", " ") if v.salary_from else ""
        mark = f"{sc.score}%" + (" предв." if sc.method == "basic" else "")
        line = (f"\n{i}. <b>{mark}</b> <a href=\"{html.escape(v.url)}\">{html.escape(v.title)}</a>\n"
                f"{html.escape(v.company_name)} · {html.escape(v.city or '')}{salary}")
        if sc.method == "ai":
            line += f"\n✅ {html.escape('; '.join(sc.pros[:2]))}\n⚠️ {html.escape('; '.join(sc.gaps[:2]))}"
        lines.append(line)
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
        rows = []
        if p is not None:
            rows = (await s.execute(select(CompanyScore, Company).join(Company, Company.id == CompanyScore.company_id)
                                    .where(CompanyScore.user_id == user.id, CompanyScore.profile_version == p.version)
                                    .order_by(CompanyScore.score.desc()).limit(10))).all()
        catalog = [] if rows else (await s.execute(
            select(Company).where(Company.status == "active", Company.careers_url.is_not(None))
            .order_by(Company.name).limit(15))).scalars().all()
    if rows:
        lines = ["<b>Подходящие компании</b>"]
        for sc, c in rows:
            link = f" · <a href=\"{html.escape(c.careers_url)}\">вакансии</a>" if c.careers_url else ""
            lines.append(f"\n<b>{sc.score}%</b> {html.escape(c.name)}{link}\n{html.escape('; '.join(sc.reasons[:2]))}")
    else:
        lines = [STUB.format(what="Подбор компаний под вас"), "", "<b>Компании с открытым разделом вакансий</b>"]
        lines += [f"• <a href=\"{html.escape(c.careers_url)}\">{html.escape(c.name)}</a>" for c in catalog]
        if not catalog:
            lines.append("Каталог ещё проверяется, загляните позже.")
    await message.answer("\n".join(lines), disable_web_page_preview=True,
                         reply_markup=webapp_kb("companies", "Весь каталог"))


# ---------- Пакет ----------

@dp.callback_query(F.data.startswith("pkg:"))
async def on_package(cb: CallbackQuery):
    vacancy_id = int(cb.data.split(":")[1])
    await cb.answer()
    if not llm.available():
        async with SessionLocal() as s:
            v = await s.get(Vacancy, vacancy_id)
        await cb.message.answer(
            STUB.format(what="Генерация CV и сопроводительного письма") +
            (f"\n\nПока можно откликнуться своим резюме: {html.escape(v.url)}" if v else ""),
            disable_web_page_preview=True)
        return
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


async def run_polling() -> None:
    s = get_settings()
    if not s.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в .env")
    await init_db()
    bot = make_bot()
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
    if url := web_url():
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=url)))
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()
