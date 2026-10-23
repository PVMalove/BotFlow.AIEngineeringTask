"""Table views of the canonical ORM metadata for shared infrastructure."""

from infrastructure.db.models import Base

metadata = Base.metadata
tasks = metadata.tables["tasks"]
inbox_messages = metadata.tables["inbox_messages"]
outbox = metadata.tables["outbox"]
