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
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties

    bot = Bot(s.telegram_bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        await bot.send_message(chat_id, text, disable_web_page_preview=True)
        return True
    except Exception:
        log.exception("telegram send failed")
        return False
    finally:
        await bot.session.close()


async def notify_new_matches(session: AsyncSession, user_id: int, limit: int = 5) -> int:
    user = await session.get(User, user_id)
    if user is None or not user.tg_id:
        return 0
    q = (select(VacancyScore, Vacancy).join(Vacancy, Vacancy.id == VacancyScore.vacancy_id)
         .where(VacancyScore.user_id == user_id, VacancyScore.notified.is_(False),
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
