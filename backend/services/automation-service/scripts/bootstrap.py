"""Bootstrap command: `migrate` applies the schema and prepares the audio bucket."""

import argparse
import asyncio
from pathlib import Path

from kernel_platform.migrations import run_migrations
from kernel_platform.resources import open_s3

import infrastructure.db as db_package
from api.containers import Container
from infrastructure.s3_audio import ensure_bucket

ALEMBIC_INI = Path(db_package.__file__).parent / "alembic.ini"


async def main() -> None:
    settings = Container().settings()
    await run_migrations(settings, ALEMBIC_INI)
    print("Automation migrations applied.")
    async with open_s3(settings) as s3:
        await ensure_bucket(s3, settings.s3_bucket)
    print("Audio bucket is ready.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate"])
    parser.parse_args()
    asyncio.run(main())
