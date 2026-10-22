"""Tasks worker: consumes CreateTask commands and relays TaskCreated results.

`replay <job_id>` re-sends the TaskCreated of a job whose outbox retry exhausted.
"""

import asyncio
import inspect
import sys
from collections.abc import Awaitable, Callable
from uuid import UUID

from api.containers import Container, build_worker
from infrastructure.db.replay import replay_task_created

IDLE_SECONDS = 1.0
FAILURE_SECONDS = 5.0


async def repeat(step: Callable[[], Awaitable[int]]) -> None:
    """Run `step` forever; pause when idle and after a failure."""
    while True:
        try:
            if not await step():
                await asyncio.sleep(IDLE_SECONDS)
        except Exception:
            # Driver errors can carry credentials; report nothing but a fixed text.
            print("Outbox relay iteration failed; retrying.", flush=True)
            await asyncio.sleep(FAILURE_SECONDS)


async def consume(run: Callable[[], Awaitable[None]]) -> None:
    """Restart the consumer after a broker failure."""
    while True:
        try:
            await run()
        except Exception:
            print("Task consumer failed; retrying.", flush=True)
            await asyncio.sleep(FAILURE_SECONDS)


async def main() -> None:
    container = Container()
    try:
        resources = await container.resources.async_()
        worker = build_worker(resources)
        print("Tasks worker started.", flush=True)
        async with asyncio.TaskGroup() as group:
            group.create_task(consume(worker.consumer.run))
            group.create_task(repeat(worker.relay.execute))
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown


async def replay(job_id: UUID) -> None:
    container = Container()
    try:
        resources = await container.resources.async_()
        replayed = await replay_task_created(resources.sessions, job_id)
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown
    if not replayed:
        raise SystemExit("Nothing to replay: no unpublished TaskCreated for this job.")
    print("TaskCreated of the job is due again.")


def _replay_job_id() -> UUID:
    try:
        return UUID(sys.argv[2])
    except ValueError:
        raise SystemExit("Usage: worker.py replay <job_id (UUID)>") from None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "replay":
        asyncio.run(replay(_replay_job_id()))
    elif len(sys.argv) > 1:
        raise SystemExit("Tasks worker handlers are not implemented.")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass
        except Exception:
            raise SystemExit(
                "Tasks worker unavailable; verify its secrets and services."
            ) from None
