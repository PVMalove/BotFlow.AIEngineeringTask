"""Tasks, consumer inbox and result outbox.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("author_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("author_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_key", name="tasks_source_key_key"),
        sa.UniqueConstraint("correlation_id", name="tasks_correlation_id_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed')",
            name="tasks_status_check",
        ),
    )
    op.create_index(
        "tasks_board_order", "tasks", [sa.text("created_at DESC"), sa.text("id DESC")]
    )
    op.create_table(
        "inbox_messages",
        sa.Column("message_id", sa.Text(), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("routing_key", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Uuid()),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
    )
    op.create_index(
        "outbox_unpublished",
        "outbox",
        ["next_attempt_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_index("outbox_correlation", "outbox", ["correlation_id"])


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("inbox_messages")
    op.drop_table("tasks")
