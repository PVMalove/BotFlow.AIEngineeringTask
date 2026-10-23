"""Typed infrastructure mappings; the database schema remains migration-owned."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    Uuid,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MemberModel(Base):
    __tablename__ = "members"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=literal_column("1")
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("members.id"), nullable=True
    )
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="members_role_check"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'blocked')",
            name="members_status_check",
        ),
    )


class LoginCodeModel(Base):
    __tablename__ = "login_codes"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, nullable=False)
    member_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("members.id"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    __table_args__ = (
        Index(
            "login_codes_one_live_per_member",
            "member_id",
            unique=True,
            postgresql_where=consumed_at.is_(None) & cancelled_at.is_(None),
        ),
    )


class AuthSessionModel(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, nullable=False)
    member_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("members.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    __table_args__ = (Index("auth_sessions_member_id", "member_id"),)


class RateLimitModel(Base):
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (PrimaryKeyConstraint("key", "window_start"),)


class OutboxModel(Base):
    __tablename__ = "outbox"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=literal_column("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    __table_args__ = (
        Index(
            "outbox_unpublished",
            "next_attempt_at",
            postgresql_where=published_at.is_(None),
        ),
    )
