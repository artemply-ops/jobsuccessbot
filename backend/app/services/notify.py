"""Уведомления в Telegram о новых подходящих вакансиях."""

import html
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User, Vacancy, VacancyScore

log = logging.getLogger(__name__)


async def send_message(chat_id: int, text: str) -> bool:
    s = get_settings()
    if not s.telegram_bot_token:
        return False
    from app.tg import make_bot

    bot = make_bot()
    try:
        await bot.send_message(chat_id, text, disable_web_page_preview=True)
        return True
    except Exception:
        log.exception("telegram send failed")
        return False
    finally:
        await bot.session.close()


STAGE_KINDS = {"package", "revision", "improve"}


async def stage_done(user_id: int, kind: str, ok: bool, note: str | None = None) -> None:
    """Готовность документов, собранных на сайте или в Mini App: Web Push в браузер и сообщение в Telegram."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    from app.db import SessionLocal

    from app.services import push

    what = {"revision": "Правка внесена", "improve": "Документы доработаны"}.get(kind, "Документы готовы")
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        try:  # Web Push в браузер, даже закрытый. Текст общий, без ПДн
            await push.send(session, user_id, f"JobPilot: {what.lower()}" if ok else "JobPilot: не получилось",
                            "Откройте, чтобы посмотреть и скачать." if ok else "Откройте и повторите.")
        except Exception:  # noqa: BLE001  пуш необязателен
            log.warning("web push failed")
    s = get_settings()
    if not user or not user.tg_id or not s.telegram_bot_token:
        return
    text = (f"✅ {what}. Откройте приложение, чтобы посмотреть и скачать." if ok else
            f"⚠️ Не получилось собрать документы: {html.escape(note or 'ошибка')}. Откройте приложение и повторите.")
    url = s.public_url.rstrip("/") + "/make"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть", web_app=WebAppInfo(url=url))]]) \
        if url.startswith("https://") else None
    from app.tg import make_bot

    bot = make_bot()
    try:
        await bot.send_message(user.tg_id, text, reply_markup=kb)
    except Exception:  # noqa: BLE001  уведомление необязательно
        log.warning("stage notify failed")
    finally:
        await bot.session.close()


async def send_documents(chat_id: int, files: list[tuple[str, bytes]], text: str | None = None) -> bool:
    """Файлы пакета в чат с ботом. Нужен для Mini App: скачивание из окна Telegram работает плохо."""
    s = get_settings()
    if not s.telegram_bot_token:
        return False
    from aiogram.types import BufferedInputFile

    from app.tg import make_bot

    bot = make_bot()
    try:
        if text:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
        for name, data in files:
            await bot.send_document(chat_id, BufferedInputFile(data, name))
        return True
    except Exception:
        log.exception("telegram send_documents failed: chat=%s files=%s", chat_id, len(files))
        return False
    finally:
        await bot.session.close()


async def notify_new_matches(session: AsyncSession, user_id: int, limit: int = 5) -> int:
    user = await session.get(User, user_id)
    if user is None or not user.tg_id:
        return 0
    q = (select(VacancyScore, Vacancy).join(Vacancy, Vacancy.id == VacancyScore.vacancy_id)
         .where(VacancyScore.user_id == user_id, VacancyScore.notified.is_(False), VacancyScore.method == "ai",
                VacancyScore.score >= user.notify_threshold)
         .order_by(VacancyScore.score.desc()))
    rows = (await session.execute(q)).all()
    if not rows:
        return 0
    lines = [f"Нашлось {len(rows)} подходящих вакансий (от {user.notify_threshold}%):", ""]
    for sc, v in rows[:limit]:
        lines.append(f"<b>{sc.score}%</b> · <a href=\"{html.escape(v.url)}\">{html.escape(v.title)}</a> — "
                     f"{html.escape(v.company_name)}")
    if len(rows) > limit:
        lines.append(f"…и ещё {len(rows) - limit}. Все в /vacancies")
    if await send_message(user.tg_id, "\n".join(lines)):
        for sc, _ in rows:
            sc.notified = True
    return len(rows)
