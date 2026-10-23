"""Export only job identities and their known source kinds for a one-off backfill.

`python -m api.export_task_sources > sources.json`; the Tasks service reads it with
`python -m api.import_task_sources`.
"""

import asyncio
import json

from kernel_platform.worker import shutdown
from sqlalchemy import select

from api.containers import Container
from infrastructure.db.models import JobModel


async def main() -> None:
    container = Container()
    try:
        database = await container.database.async_()
        async with database.sessions() as session:
            rows = await session.execute(select(JobModel.id, JobModel.kind))
            print(
                json.dumps(
                    [{"job_id": str(row.id), "source_type": row.kind} for row in rows]
                )
            )
    finally:
        await shutdown(container)


if __name__ == "__main__":
    asyncio.run(main())
