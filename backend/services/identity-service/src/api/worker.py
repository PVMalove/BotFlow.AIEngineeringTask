"""Identity worker: relays stored events from the outbox to RabbitMQ."""

import asyncio
import inspect
from datetime import UTC, datetime

from kernel_platform.messaging import ConfirmedPublisher

from api.containers import Container
from application.commands.relay import RelayOutbox
from infrastructure.db.unit_of_work import SqlUnitOfWork
from infrastructure.messaging.publisher import RabbitEventPublisher

IDLE_SECONDS = 1.0
FAILURE_SECONDS = 5.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def main() -> None:
    container = Container()
    try:
        resources = await container.resources.async_()
        relay = RelayOutbox(
            lambda: SqlUnitOfWork(resources.sessions),
            RabbitEventPublisher(ConfirmedPublisher(resources.rabbitmq)),
            _utcnow,
        )
        print("Identity outbox relay started.", flush=True)
        while True:
            try:
                published = await relay.execute()
            except Exception:
                # Driver errors can carry credentials; report a fixed message only.
                print("Outbox relay iteration failed; retrying.", flush=True)
                await asyncio.sleep(FAILURE_SECONDS)
            else:
                if not published:
                    await asyncio.sleep(IDLE_SECONDS)
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
            "Identity worker unavailable; verify its secrets and services."
        ) from None
