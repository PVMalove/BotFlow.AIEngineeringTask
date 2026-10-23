"""Typed infrastructure mappings; the database schema remains migration-owned."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from kernel_platform.inbox import InboxColumns
from kernel_platform.outbox import OutboxColumns
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    literal_column,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from domain.value_objects.job_status import JobKind


class Base(DeclarativeBase):
    pass


class TelegramUpdateModel(Base):
    __tablename__ = "telegram_updates"
    update_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class JobModel(Base):
    __tablename__ = "jobs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, nullable=False)
    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    author_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_name: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=JobKind.TEXT.value
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    acked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("bot_id", "chat_id", "message_id", name="jobs_source_key"),
        CheckConstraint(
            "status IN ('accepted', 'completed', 'failed')", name="jobs_status_check"
        ),
        CheckConstraint("kind IN ('text', 'voice')", name="jobs_kind_check"),
        CheckConstraint("kind = 'voice' OR text IS NOT NULL", name="jobs_text_present"),
        Index("jobs_unfinished", "author_id", postgresql_where=status == "accepted"),
    )
    voice: Mapped[VoiceJobModel | None] = relationship(
        back_populates="job",
        uselist=False,
        lazy="raise",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class VoiceJobModel(Base):
    __tablename__ = "voice_jobs"
    job_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    file_unique_id: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    audio_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=literal_column("0")
    )
    cold_start: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stt_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stt_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    __table_args__ = (
        CheckConstraint(
            "stage IN ('queued', 'stored', 'done', 'failed')",
            name="voice_jobs_stage_check",
        ),
        Index(
            "voice_jobs_unfinished",
            "stage",
            postgresql_where=stage.in_(["queued", "stored"]),
        ),
    )
    job: Mapped[JobModel] = relationship(back_populates="voice", lazy="raise")


class OutboxModel(OutboxColumns, Base):
    __tablename__ = "outbox"


class InboxMessageModel(InboxColumns, Base):
    __tablename__ = "inbox_messages"


class NotificationIntentModel(Base):
    __tablename__ = "notification_intents"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    member_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    access_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=literal_column("0")
    )
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'enqueued', 'sent', 'suppressed_stale', "
            "'suppressed_blocked', 'failed')",
            name="notification_intents_status_check",
        ),
        Index(
            "notification_intents_pending",
            "created_at",
            postgresql_where=status == "pending",
        ),
    )
