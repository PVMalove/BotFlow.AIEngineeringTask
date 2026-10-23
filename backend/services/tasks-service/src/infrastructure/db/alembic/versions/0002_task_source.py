"""Preserve known Telegram message types; old tasks remain unknown.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("source_type", sa.Text(), nullable=True))
    op.create_check_constraint(
        "tasks_source_type_check", "tasks", "source_type IN ('text', 'voice')"
    )


def downgrade() -> None:
    op.drop_constraint("tasks_source_type_check", "tasks", type_="check")
    op.drop_column("tasks", "source_type")
