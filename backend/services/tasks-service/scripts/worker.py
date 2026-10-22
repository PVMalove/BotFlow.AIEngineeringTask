"""Tasks worker: consumes CreateTask commands and relays TaskCreated results."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable

from api.containers import Container, build_worker

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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit(
            "Tasks worker unavailable; verify its secrets and services."
        ) from None
