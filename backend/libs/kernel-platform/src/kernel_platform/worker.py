"""Shared loops and process entry of the background workers."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

IDLE_SECONDS = 1.0
FAILURE_SECONDS = 5.0


async def repeat(
    step: Callable[[], Awaitable[int]],
    *,
    failure: str = "Worker step failed; retrying.",
) -> None:
    """Run `step` forever; pause when it did nothing and after a failure.

    Driver errors can carry credentials, so a failure prints a fixed text only."""
    while True:
        try:
            if not await step():
                await asyncio.sleep(IDLE_SECONDS)
        except Exception:
            print(failure, flush=True)
            await asyncio.sleep(FAILURE_SECONDS)


async def forever(
    run: Callable[[], Awaitable[None]],
    *,
    failure: str = "Worker consumer failed; retrying.",
) -> None:
    """Restart a consumer after a broker failure."""
    while True:
        try:
            await run()
        except Exception:
            print(failure, flush=True)
            await asyncio.sleep(FAILURE_SECONDS)


async def shutdown(container: Any) -> None:
    """Release the resources of a dependency-injector container."""
    pending = container.shutdown_resources()
    if inspect.isawaitable(pending):
        await pending


def run_process(
    main: Callable[[], Coroutine[Any, Any, None]], *, unavailable: str
) -> None:
    """Run `main` as the process entry: Ctrl+C is quiet, any other failure is
    reported with the fixed `unavailable` text."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit(unavailable) from None
