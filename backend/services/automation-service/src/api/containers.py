"""Service composition root; each process owns its infrastructure lifecycle."""

from botflow_platform.resources import Database, open_database, open_resources
from botflow_platform.settings import Settings
from dependency_injector import containers, providers

from application.commands.handle_bot_command import HandleBotCommand
from application.ports import IdentityGateway, Messenger
from infrastructure.db.inbox import SqlUpdateInbox


def build_bot_command(
    database: Database, identity: IdentityGateway, messenger: Messenger
) -> HandleBotCommand:
    return HandleBotCommand(identity, messenger, SqlUpdateInbox(database.sessions))


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(Settings, service="automation")
    resources = providers.Resource(open_resources, settings=settings)
    # CLI processes (bootstrap, bot) need only the database, not RabbitMQ or S3.
    database = providers.Resource(open_database, settings=settings)

    bot_command = providers.Factory(build_bot_command, database=database)
