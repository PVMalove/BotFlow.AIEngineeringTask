"""Table views of the canonical ORM metadata for shared infrastructure."""

from infrastructure.db.models import Base

metadata = Base.metadata
members = metadata.tables["members"]
login_codes = metadata.tables["login_codes"]
auth_sessions = metadata.tables["auth_sessions"]
rate_limits = metadata.tables["rate_limits"]
outbox = metadata.tables["outbox"]
