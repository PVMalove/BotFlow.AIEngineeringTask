"""The automation tables: jobs and their voice records, inbox, intents and outbox."""

from kernel_platform.outbox import outbox_table
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)

from domain.value_objects.job_status import JobKind
from infrastructure.db.inbox import metadata

jobs = Table(
    "jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("bot_id", BigInteger, nullable=False),
    Column("chat_id", BigInteger, nullable=False),
    Column("message_id", BigInteger, nullable=False),
    Column("author_id", Uuid, nullable=False),
    Column("author_telegram_id", BigInteger, nullable=False),
    Column("author_name", Text, nullable=False),
    Column("text", Text),  # a voice job gets its text from the transcript
    Column("kind", Text, nullable=False, server_default=JobKind.TEXT.value),
    Column("status", Text, nullable=False),
    Column("task_id", Uuid),
    Column("last_error", Text),
    Column("message_date", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("acked_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("bot_id", "chat_id", "message_id", name="jobs_source_key"),
    CheckConstraint(
        "status IN ('accepted', 'completed', 'failed')", name="jobs_status_check"
    ),
    CheckConstraint("kind IN ('text', 'voice')", name="jobs_kind_check"),
    CheckConstraint("kind = 'voice' OR text IS NOT NULL", name="jobs_text_present"),
    Index(
        "jobs_unfinished",
        "author_id",
        postgresql_where=text("status = 'accepted'"),
    ),
)

voice_jobs = Table(
    "voice_jobs",
    metadata,
    Column("job_id", Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
    Column("file_id", Text, nullable=False),
    Column("file_unique_id", Text, nullable=False),
    Column("duration_seconds", Integer, nullable=False),
    Column("file_size_bytes", BigInteger),
    Column("mime_type", Text),
    Column("stage", Text, nullable=False),
    Column("s3_key", Text),
    Column("audio_bytes", BigInteger),
    Column("audio_sha256", Text),
    Column("language", Text),
    Column("error_code", Text),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("cold_start", Boolean, nullable=False, server_default=text("false")),
    Column("started_at", DateTime(timezone=True)),
    Column("stored_at", DateTime(timezone=True)),
    Column("stt_started_at", DateTime(timezone=True)),
    Column("stt_finished_at", DateTime(timezone=True)),
    CheckConstraint(
        "stage IN ('queued', 'stored', 'done', 'failed')",
        name="voice_jobs_stage_check",
    ),
    Index(
        "voice_jobs_unfinished",
        "stage",
        postgresql_where=text("stage IN ('queued', 'stored')"),
    ),
)

outbox = outbox_table(metadata)

inbox_messages = Table(
    "inbox_messages",
    metadata,
    Column("message_id", Text, primary_key=True),
    Column("received_at", DateTime(timezone=True), nullable=False),
)

notification_intents = Table(
    "notification_intents",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("chat_id", BigInteger, nullable=False),
    Column("member_id", Uuid, nullable=False),
    Column("kind", Text, nullable=False),
    Column("access_version", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("body", Text),  # text of a result notification
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status IN ('pending', 'enqueued', 'sent', 'suppressed_stale', "
        "'suppressed_blocked', 'failed')",
        name="notification_intents_status_check",
    ),
    Index(
        "notification_intents_pending",
        "created_at",
        postgresql_where=text("status = 'pending'"),
    ),
)
