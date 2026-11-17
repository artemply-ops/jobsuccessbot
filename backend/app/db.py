from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Схема БД. С 17.11.2026 изменения идут миграциями Alembic (backend/migrations, Т3).
    Новая база: create_all по моделям и отметка последней ревизии. Существующая: сначала миграции,
    потом create_all и досоздание колонок как страховка (если миграцию забыли, приложение всё равно поднимется)."""
    from app import models  # noqa: F401  регистрация таблиц

    async with engine.begin() as conn:
        fresh = not await conn.run_sync(lambda c: _inspect(c).has_table("users"))
        if not fresh:
            await conn.run_sync(_migrate, False)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
        await conn.run_sync(_widen_columns)
        if fresh:
            await conn.run_sync(_migrate, True)


def _inspect(conn):
    from sqlalchemy import inspect

    return inspect(conn)


def _migrate(conn, fresh: bool) -> None:
    """Базе без отметки Alembic ставим базовую ревизию (0001, прод до 17.11) или последнюю (новая база), затем upgrade."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.attributes["connection"] = conn
    if not _inspect(conn).has_table("alembic_version"):
        command.stamp(cfg, "head" if fresh else "0001")
    command.upgrade(cfg, "head")


def _add_missing_columns(conn) -> None:
    """Миграций (Alembic) пока нет: новые nullable-колонки досоздаём на существующих таблицах."""
    from sqlalchemy import inspect

    insp = inspect(conn)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have and col.nullable:
                ddl = col.type.compile(dialect=conn.dialect)
                conn.exec_driver_sql(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}')


def _widen_columns(conn) -> None:
    """15.11.2026: users.tg_id из INTEGER в BIGINT (id Telegram бывают больше 2^31). Только Postgres, SQLite не нужно."""
    if conn.dialect.name != "postgresql":
        return
    t = conn.exec_driver_sql("select data_type from information_schema.columns "
                             "where table_name='users' and column_name='tg_id'").scalar()
    if t == "integer":
        conn.exec_driver_sql("ALTER TABLE users ALTER COLUMN tg_id TYPE BIGINT")


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
