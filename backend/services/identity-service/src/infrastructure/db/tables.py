"""Identity-owned schema; Alembic revisions mirror it explicitly."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    Uuid,
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
    CheckConstraint("role IN ('admin', 'user')", name="members_role_check"),
    CheckConstraint(
        "status IN ('pending', 'approved', 'rejected', 'blocked')",
        name="members_status_check",
    ),
)
