"""Automation migration environment; run through `scripts/bootstrap.py migrate`."""

from botflow_platform.migrations import configure_environment
from botflow_platform.settings import Settings

from infrastructure.db.inbox import metadata

configure_environment(metadata, Settings(service="automation"))
