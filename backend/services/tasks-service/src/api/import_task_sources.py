"""Restore unknown sources without changing task status or version.

Reads `[{"job_id": ..., "source_type": ...}]` from stdin:
`python -m api.import_task_sources < sources.json`.
"""

import asyncio
import json
import sys
from uuid import UUID

from kernel_platform.worker import shutdown
from sqlalchemy import update

from api.containers import Container
from domain.value_objects.task_source import TaskSource
from infrastructure.db.tables import tasks


async def main() -> None:
    # Validate the whole input before opening a transaction. No task text or author
    # data crosses the service boundary; each process reads only its own database.
    rows = [
        (UUID(row["job_id"]), TaskSource(row["source_type"]))
        for row in json.load(sys.stdin)
    ]
    changed = 0
    container = Container()
    try:
        resources = await container.resources.async_()
        async with resources.sessions() as session:
            for job_id, source_type in rows:
                result = await session.execute(
                    update(tasks)
                    .where(
                        tasks.c.correlation_id == job_id, tasks.c.source_type.is_(None)
                    )
                    .values(source_type=source_type.value)
                    .returning(tasks.c.id)
                )
                changed += len(result.all())
            await session.commit()
    finally:
        await shutdown(container)
    print(f"Task source types restored: {changed}.")


if __name__ == "__main__":
    asyncio.run(main())
