"""Voice jobs: job kind, optional text and the voice record.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("kind", sa.Text(), nullable=False, server_default="text"),
    )
    op.alter_column("jobs", "text", nullable=True)
    op.create_check_constraint("jobs_kind_check", "jobs", "kind IN ('text', 'voice')")
    op.create_check_constraint(
        "jobs_text_present", "jobs", "kind = 'voice' OR text IS NOT NULL"
    )
    op.create_table(
        "voice_jobs",
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("file_unique_id", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger()),
        sa.Column("mime_type", sa.Text()),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("s3_key", sa.Text()),
        sa.Column("audio_bytes", sa.BigInteger()),
        sa.Column("audio_sha256", sa.Text()),
        sa.Column("language", sa.Text()),
        sa.Column("error_code", sa.Text()),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "cold_start",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stored_at", sa.DateTime(timezone=True)),
        sa.Column("stt_started_at", sa.DateTime(timezone=True)),
        sa.Column("stt_finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "stage IN ('queued', 'stored', 'done', 'failed')",
            name="voice_jobs_stage_check",
        ),
    )
    op.create_index(
        "voice_jobs_unfinished",
        "voice_jobs",
        ["stage"],
        postgresql_where=sa.text("stage IN ('queued', 'stored')"),
    )


def downgrade() -> None:
    op.drop_constraint("jobs_text_present", "jobs", type_="check")
    op.drop_constraint("jobs_kind_check", "jobs", type_="check")
    op.execute(
        """
        UPDATE jobs
        SET text = COALESCE(
            NULLIF(jobs.text, ''),
            '[Голосовое сообщение: '
            || v.duration_seconds
            || ' с, file_id: '
            || v.file_id
            || ']'
        )
        FROM voice_jobs v
        WHERE jobs.id = v.job_id AND (jobs.text IS NULL OR jobs.text = '')
        """
    )
    op.execute("UPDATE jobs SET text = '[Голосовое сообщение]' WHERE text IS NULL")
    op.drop_table("voice_jobs")
    op.alter_column("jobs", "text", nullable=False)
    op.drop_column("jobs", "kind")
