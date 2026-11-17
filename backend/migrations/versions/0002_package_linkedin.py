"""П3: профиль LinkedIn в пакете (packages.linkedin).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("packages") as b:
        b.add_column(sa.Column("linkedin", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("packages") as b:
        b.drop_column("linkedin")
