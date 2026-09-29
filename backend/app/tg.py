"""Фабрика Telegram-бота. Из РФ Telegram API доступен через прокси (PROXY_URL)."""

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

from app.config import get_settings


def make_bot() -> Bot:
    s = get_settings()
    session = AiohttpSession(proxy=s.proxy_url) if s.proxy_url else None
    return Bot(s.telegram_bot_token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
