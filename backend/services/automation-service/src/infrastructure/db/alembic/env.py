"""Automation migration environment; run through `scripts/bootstrap.py migrate`."""

from kernel_platform.migrations import configure_environment
from kernel_platform.settings import Settings

from infrastructure.db.inbox import metadata
from infrastructure.db.jobs import jobs  # noqa: F401
from infrastructure.db.notifications import notification_intents  # noqa: F401

configure_environment(metadata, Settings(service="automation"))
