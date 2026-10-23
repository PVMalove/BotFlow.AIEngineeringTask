"""Members double as join requests: display name, access version, last decision.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("display_name", sa.Text()))
    op.add_column(
        "members",
        sa.Column(
            "access_version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column("members", sa.Column("status_changed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "members",
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("members.id")),
    )


def downgrade() -> None:
    op.drop_column("members", "decided_by")
    op.drop_column("members", "status_changed_at")
    op.drop_column("members", "access_version")
    op.drop_column("members", "display_name")
