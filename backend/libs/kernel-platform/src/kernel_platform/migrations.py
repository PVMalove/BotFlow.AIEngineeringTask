"""Alembic runner shared by service bootstrap commands; revisions stay service-owned."""

import asyncio
from pathlib import Path

from alembic import command, context
from alembic.config import Config
from sqlalchemy import MetaData, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from kernel_platform.resources import open_database
from kernel_platform.settings import Settings

# Serializes concurrent bootstrap processes; released when the transaction ends.
_MIGRATION_LOCK_KEY = 7_204_121


async def run_migrations(settings: Settings, alembic_ini: Path) -> None:
    """Upgrade the service database to head in one locked transaction."""
    config = Config(str(alembic_ini))
    async with open_database(settings) as database, database.engine.begin() as conn:
        await conn.run_sync(_upgrade, config)


def configure_environment(metadata: MetaData, settings: Settings) -> None:
    """Body of a service `env.py`: reuse the runner's connection or open one."""
    if context.is_offline_mode():
        raise RuntimeError("Offline migrations are not supported.")
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _migrate(connection, metadata)
        return

    async def standalone() -> None:
        engine = create_async_engine(settings.database_url())
        try:
            async with engine.connect() as conn:
                await conn.run_sync(_migrate, metadata)
                await conn.commit()
        finally:
            await engine.dispose()

    asyncio.run(standalone())


def _upgrade(connection: Connection, config: Config) -> None:
    connection.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK_KEY}
    )
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def _migrate(connection: Connection, metadata: MetaData) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
