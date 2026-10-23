"""Table views of the canonical ORM metadata for shared infrastructure."""

from infrastructure.db.models import Base

metadata = Base.metadata
jobs = metadata.tables["jobs"]
voice_jobs = metadata.tables["voice_jobs"]
outbox = metadata.tables["outbox"]
inbox_messages = metadata.tables["inbox_messages"]
notification_intents = metadata.tables["notification_intents"]
