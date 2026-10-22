"""Login codes, web sessions and the attempt counter.

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
    op.create_table(
        "login_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "login_codes_one_live_per_member",
        "login_codes",
        ["member_id"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL AND cancelled_at IS NULL"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("auth_sessions_member_id", "auth_sessions", ["member_id"])
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "window_start"),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")
    op.drop_table("auth_sessions")
    op.drop_table("login_codes")
