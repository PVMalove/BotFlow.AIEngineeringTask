"""Bootstrap commands: `migrate` applies the schema, `admin` ensures the first admin."""

import argparse
import asyncio
import os
from pathlib import Path

from kernel_platform.clock import utcnow
from kernel_platform.migrations import run_migrations

import infrastructure.db as db_package
from api.containers import Container
from application.commands.auth import (
    EnsureInitialAdminCommand,
    EnsureInitialAdminCommandHandler,
)
from infrastructure.db.unit_of_work import SqlUnitOfWork

ALEMBIC_INI = Path(db_package.__file__).parent / "alembic.ini"


async def migrate(container: Container) -> None:
    await run_migrations(container.settings(), ALEMBIC_INI)
    print("Identity migrations applied.")


async def admin(container: Container) -> None:
    raw = os.environ.get("INITIAL_ADMIN_TELEGRAM_ID", "").strip()
    if not raw.isdigit() or int(raw) == 0:
        raise SystemExit("INITIAL_ADMIN_TELEGRAM_ID must be a positive Telegram ID.")
    database = await container.database.async_()
    ensure_admin = EnsureInitialAdminCommandHandler(
        lambda: SqlUnitOfWork(database.sessions), utcnow
    )
    if await ensure_admin.execute(EnsureInitialAdminCommand(telegram_id=int(raw))):
        print("Initial admin created.")
    else:
        print("Initial admin already exists; access decisions are unchanged.")


async def main(command: str) -> None:
    container = Container()
    try:
        await {"migrate": migrate, "admin": admin}[command](container)
    finally:
        closing = container.shutdown_resources()
        if closing is not None:
            await closing


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate", "admin"])
    asyncio.run(main(parser.parse_args().command))
