"""Jobs, their outbox and the notification text.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("author_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("author_name", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Uuid()),
        sa.Column("last_error", sa.Text()),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bot_id", "chat_id", "message_id", name="jobs_source_key"),
        sa.CheckConstraint(
            "status IN ('accepted', 'completed', 'failed')", name="jobs_status_check"
        ),
    )
    op.create_index(
        "jobs_unfinished",
        "jobs",
        ["author_id"],
        postgresql_where=sa.text("status = 'accepted'"),
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
    op.add_column("notification_intents", sa.Column("body", sa.Text()))


def downgrade() -> None:
    op.drop_column("notification_intents", "body")
    op.drop_table("outbox")
    op.drop_table("jobs")
