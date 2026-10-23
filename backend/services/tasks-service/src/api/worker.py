"""Tasks worker: consumes CreateTask commands and relays TaskCreated results.

`replay <job_id>` re-sends the TaskCreated of a job whose outbox retry exhausted.
"""

import asyncio
import sys
from uuid import UUID

from kernel_platform.worker import forever, repeat, run_process, shutdown

from api.containers import Container, build_worker
from infrastructure.db.replay import replay_task_created


async def main() -> None:
    container = Container()
    try:
        resources = await container.resources.async_()
        worker = build_worker(resources)
        print("Tasks worker started.", flush=True)
        async with asyncio.TaskGroup() as group:
            group.create_task(
                forever(worker.consumer.run, failure="Task consumer failed; retrying.")
            )
            group.create_task(
                repeat(
                    worker.relay.execute,
                    failure="Outbox relay iteration failed; retrying.",
                )
            )
    finally:
        await shutdown(container)


async def replay(job_id: UUID) -> None:
    container = Container()
    try:
        resources = await container.resources.async_()
        replayed = await replay_task_created(resources.sessions, job_id)
    finally:
        await shutdown(container)
    if not replayed:
        raise SystemExit("Nothing to replay: no unpublished TaskCreated for this job.")
    print("TaskCreated of the job is due again.")


def _replay_job_id() -> UUID:
    try:
        return UUID(sys.argv[2])
    except ValueError:
        raise SystemExit("Usage: python -m api.worker replay <job_id (UUID)>") from None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "replay":
        asyncio.run(replay(_replay_job_id()))
    elif len(sys.argv) > 1:
        raise SystemExit("Tasks worker handlers are not implemented.")
    else:
        run_process(
            main,
            unavailable="Tasks worker unavailable; verify its secrets and services.",
        )
