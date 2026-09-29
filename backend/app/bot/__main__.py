"""Отдельный запуск бота: python -m app.bot (на сервере бот работает внутри API, см. RUN_BOT_IN_API)."""

import asyncio
import logging

from app.bot.handlers import run_polling

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_polling())
