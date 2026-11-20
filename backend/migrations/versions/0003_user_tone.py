"""П13: стиль общения сервиса (users.tone: neutral | ty | vy, пусто = нейтрально).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("tone", sa.String(length=8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.drop_column("tone")
