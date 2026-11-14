"""Отдельный запуск бота: python -m app.bot (на сервере бот работает внутри API, см. RUN_BOT_IN_API)."""

import asyncio

from app.bot.handlers import run_polling
from app.logs import setup_logging

if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_polling())
