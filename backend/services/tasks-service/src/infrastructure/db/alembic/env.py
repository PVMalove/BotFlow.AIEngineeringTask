"""Tasks migration environment; run through `python -m api.bootstrap migrate`."""

from kernel_platform.migrations import configure_environment
from kernel_platform.settings import Settings

from infrastructure.db.tables import metadata

configure_environment(metadata, Settings(service="tasks"))
