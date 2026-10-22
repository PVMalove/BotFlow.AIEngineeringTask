"""Identity-owned schema; Alembic revisions mirror it explicitly."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    Uuid,
    text,
)

metadata = MetaData()

members = Table(
    "members",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("telegram_id", BigInteger, nullable=False, unique=True),
    Column("role", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("display_name", Text),
    Column("access_version", Integer, nullable=False, server_default=text("1")),
    Column("status_changed_at", DateTime(timezone=True)),
    Column("decided_by", Uuid, ForeignKey("members.id")),
    CheckConstraint("role IN ('admin', 'user')", name="members_role_check"),
    CheckConstraint(
        "status IN ('pending', 'approved', 'rejected', 'blocked')",
        name="members_status_check",
    ),
)

login_codes = Table(
    "login_codes",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("member_id", Uuid, ForeignKey("members.id"), nullable=False),
    Column("code_hash", Text, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
    Column("cancelled_at", DateTime(timezone=True)),
    # At most one live code per member, even under concurrent issuance.
    Index(
        "login_codes_one_live_per_member",
        "member_id",
        unique=True,
        postgresql_where=text("consumed_at IS NULL AND cancelled_at IS NULL"),
    ),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("member_id", Uuid, ForeignKey("members.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    Index("auth_sessions_member_id", "member_id"),
)

rate_limits = Table(
    "rate_limits",
    metadata,
    Column("key", Text, nullable=False),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("count", Integer, nullable=False),
    PrimaryKeyConstraint("key", "window_start"),
)
