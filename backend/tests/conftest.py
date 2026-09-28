import os
import tempfile
from pathlib import Path

# Отдельная БД и выключенная LLM для тестов: тесты не ходят во внешние сервисы
_tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["FILES_DIR"] = str(_tmp / "files")
os.environ["LLM_ENABLED"] = "false"
os.environ["ENV"] = "dev"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST"
os.environ["STT_PROVIDER"] = "none"

import pytest  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402


@pytest.fixture
async def session():
    await init_db()
    async with SessionLocal() as s:
        yield s
        await s.rollback()
