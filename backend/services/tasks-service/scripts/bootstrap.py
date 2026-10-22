"""Bootstrap command: `migrate` applies the Tasks schema."""

import argparse
import asyncio
from pathlib import Path

from kernel_platform.migrations import run_migrations

import infrastructure.db as db_package
from api.containers import Container

ALEMBIC_INI = Path(db_package.__file__).parent / "alembic.ini"


async def main() -> None:
    await run_migrations(Container().settings(), ALEMBIC_INI)
    print("Tasks migrations applied.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate"])
    parser.parse_args()
    asyncio.run(main())
