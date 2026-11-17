"""Базовая точка: схема на 17.11.2026, как её создают create_all и прежние досоздания колонок.

Существующая база (прод) помечается этой ревизией без изменений, дальше идут обычные миграции.

Revision ID: 0001
Revises:
Create Date: 2026-11-17
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
