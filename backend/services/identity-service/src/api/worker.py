"""Identity worker: relays stored events from the outbox to RabbitMQ."""

from kernel_platform.clock import utcnow
from kernel_platform.messaging import ConfirmedPublisher
from kernel_platform.worker import repeat, run_process, shutdown

from api.containers import Container
from application.commands.relay import RelayOutbox
from infrastructure.db.unit_of_work import SqlUnitOfWork
from infrastructure.messaging.publisher import RabbitEventPublisher


async def main() -> None:
    container = Container()
    try:
        resources = await container.resources.async_()
        relay = RelayOutbox(
            lambda: SqlUnitOfWork(resources.sessions),
            RabbitEventPublisher(ConfirmedPublisher(resources.rabbitmq)),
            utcnow,
        )
        print("Identity outbox relay started.", flush=True)
        await repeat(relay.execute, failure="Outbox relay iteration failed; retrying.")
    finally:
        await shutdown(container)


if __name__ == "__main__":
    run_process(
        main,
        unavailable="Identity worker unavailable; verify its secrets and services.",
    )
