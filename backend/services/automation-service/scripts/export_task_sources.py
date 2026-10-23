"""Export only job identities and their known source kinds for a one-off backfill."""

import asyncio
import json

from botflow_platform.resources import open_database
from botflow_platform.settings import Settings
from sqlalchemy import select

from infrastructure.db.jobs import jobs


async def main() -> None:
    async with open_database(Settings(service="automation")) as database:
        async with database.sessions() as session:
            rows = await session.execute(select(jobs.c.id, jobs.c.kind))
            print(
                json.dumps(
                    [{"job_id": str(row.id), "source_type": row.kind} for row in rows]
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
