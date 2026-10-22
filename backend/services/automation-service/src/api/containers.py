"""Service composition root; each process owns its infrastructure lifecycle."""

from dataclasses import dataclass

from botflow_platform.resources import (
    Database,
    Resources,
    open_database,
    open_resources,
)
from botflow_platform.settings import Settings
from botflow_platform.streams import RedisStream
from dependency_injector import containers, providers

from application.commands.deliver_notification import DeliverNotification, RunNotifier
from application.commands.handle_bot_command import HandleBotCommand
from application.commands.process_identity_event import ProcessIdentityEvent
from application.commands.relay_intents import RelayIntents
from application.ports import IdentityGateway, Messenger
from infrastructure.db.inbox import SqlUpdateInbox
from infrastructure.db.notifications import SqlIntentStore
from infrastructure.messaging import IdentityEventConsumer
from infrastructure.streams import (
    NOTIFICATION_GROUP,
    NOTIFICATION_STREAM,
    STUCK_AFTER_MS,
    RedisNotificationQueue,
)


def build_bot_command(
    database: Database, identity: IdentityGateway, messenger: Messenger
) -> HandleBotCommand:
    return HandleBotCommand(identity, messenger, SqlUpdateInbox(database.sessions))


@dataclass(frozen=True, slots=True)
class NotificationPipeline:
    """RabbitMQ -> PostgreSQL intent -> Redis Stream -> Telegram, as separate steps."""

    consumer: IdentityEventConsumer
    relay: RelayIntents
    notifier: RunNotifier
    queue: RedisNotificationQueue


def build_notifications(
    resources: Resources,
    identity: IdentityGateway,
    messenger: Messenger,
    *,
    consumer_name: str,
    stuck_after_ms: int = STUCK_AFTER_MS,
) -> NotificationPipeline:
    if resources.redis is None:
        raise RuntimeError("Notifications need the Redis connection.")
    store = SqlIntentStore(resources.sessions)
    queue = RedisNotificationQueue(
        RedisStream(
            resources.redis, NOTIFICATION_STREAM, NOTIFICATION_GROUP, consumer_name
        ),
        stuck_after_ms,
    )
    return NotificationPipeline(
        consumer=IdentityEventConsumer(resources.rabbitmq, ProcessIdentityEvent(store)),
        relay=RelayIntents(store, queue),
        notifier=RunNotifier(queue, DeliverNotification(store, identity, messenger)),
        queue=queue,
    )


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(Settings, service="automation")
    resources = providers.Resource(open_resources, settings=settings)
    # CLI processes (bootstrap, bot) need only the database, not RabbitMQ or S3.
    database = providers.Resource(open_database, settings=settings)

    bot_command = providers.Factory(build_bot_command, database=database)
