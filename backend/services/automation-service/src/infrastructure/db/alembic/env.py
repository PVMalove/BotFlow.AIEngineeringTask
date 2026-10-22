"""Automation migration environment; run through `python -m api.bootstrap migrate`."""

from kernel_platform.migrations import configure_environment
from kernel_platform.settings import Settings

from infrastructure.db import tables  # noqa: F401
from infrastructure.db.inbox import metadata

configure_environment(metadata, Settings(service="automation"))
