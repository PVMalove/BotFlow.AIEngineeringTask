"""Identity migration environment; run through `python -m api.bootstrap migrate`."""

from kernel_platform.migrations import configure_environment
from kernel_platform.settings import Settings

from infrastructure.db.models import Base

configure_environment(Base.metadata, Settings(service="identity"))
