"""Ports of the application layer."""

from collections.abc import Callable
from datetime import datetime

from domain.unit_of_work import UnitOfWork

Clock = Callable[[], datetime]
UnitOfWorkFactory = Callable[[], UnitOfWork]
