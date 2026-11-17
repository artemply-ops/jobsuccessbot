"""Окружение Alembic. Два режима:
- из приложения (db.init_db): соединение передано в config.attributes["connection"];
- из командной строки (alembic revision --autogenerate / upgrade): движок из настроек приложения (DATABASE_URL).
"""

import asyncio

from alembic import context

from app import models  # noqa: F401  регистрация таблиц
from app.db import Base, engine

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite", compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_cli() -> None:
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()


conn = context.config.attributes.get("connection")
if conn is not None:
    _run(conn)
else:
    asyncio.run(_run_cli())
