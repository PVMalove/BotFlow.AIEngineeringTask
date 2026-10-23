"""Tasks storage: the board, the consumer inbox and the result outbox."""

from botflow_platform.outbox import outbox_table
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)

metadata = MetaData()

tasks = Table(
    "tasks",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("source_key", Text, nullable=False),
    Column("source_type", Text),
    Column("correlation_id", Uuid, nullable=False),
    Column("text", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("author_id", Uuid, nullable=False),
    Column("author_telegram_id", BigInteger, nullable=False),
    Column("author_name", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("source_key", name="tasks_source_key_key"),
    UniqueConstraint("correlation_id", name="tasks_correlation_id_key"),
    CheckConstraint(
        "status IN ('pending', 'in_progress', 'completed')",
        name="tasks_status_check",
    ),
    CheckConstraint("source_type IN ('text', 'voice')", name="tasks_source_type_check"),
    Index("tasks_board_order", text("created_at DESC"), text("id DESC")),
)

inbox_messages = Table(
    "inbox_messages",
    metadata,
    Column("message_id", Text, primary_key=True),
    Column("received_at", DateTime(timezone=True), nullable=False),
)

outbox = outbox_table(metadata)
