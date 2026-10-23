"""Typed infrastructure mappings; the database schema remains migration-owned."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from kernel_platform.inbox import InboxColumns
from kernel_platform.outbox import OutboxColumns
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    literal_column,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TaskModel(Base):
    __tablename__ = "tasks"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=literal_column("1")
    )
    author_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    author_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("source_key", name="tasks_source_key_key"),
        UniqueConstraint("correlation_id", name="tasks_correlation_id_key"),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed')",
            name="tasks_status_check",
        ),
        CheckConstraint(
            "source_type IN ('text', 'voice')", name="tasks_source_type_check"
        ),
        Index("tasks_board_order", created_at.desc(), id.desc()),
    )


class InboxMessageModel(InboxColumns, Base):
    __tablename__ = "inbox_messages"


class OutboxModel(OutboxColumns, Base):
    __tablename__ = "outbox"


metadata = Base.metadata
