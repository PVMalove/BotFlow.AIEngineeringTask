"""Service composition root; each process owns its infrastructure lifecycle."""

from dataclasses import dataclass

from dependency_injector import containers, providers
from kernel_platform.clock import utcnow
from kernel_platform.resources import Resources, open_database, open_resources
from kernel_platform.settings import Settings

from api.security import CookiePolicy, Operation, ServiceKeys
from application.commands.access import DecideMemberCommandHandler
from application.commands.auth import (
    ExchangeLoginCodeCommandHandler,
    IssueLoginCodeCommandHandler,
    LogoutCommandHandler,
)
from application.commands.join import SubmitJoinRequestCommandHandler
from application.queries.members import (
    AuthenticateSessionQueryHandler,
    FindMemberByTelegramQueryHandler,
    ListMembersQueryHandler,
)
from infrastructure.db.rate_limiter import SqlRateLimiter
from infrastructure.db.unit_of_work import SqlUnitOfWork
from infrastructure.security.jwt import Rs256Tokens

# Only the loopback HTTP profiles may use a cookie without the Secure attribute.
_INSECURE_COOKIE_ENVIRONMENTS = {"local", "test"}


@dataclass(frozen=True, slots=True)
class Services:
    """Use cases the HTTP layer needs, wired to the process resources."""

    exchange: ExchangeLoginCodeCommandHandler
    logout: LogoutCommandHandler
    authenticate: AuthenticateSessionQueryHandler
    cookies: CookiePolicy
    issue_code: IssueLoginCodeCommandHandler
    find_member: FindMemberByTelegramQueryHandler
    submit_join: SubmitJoinRequestCommandHandler
    decide: DecideMemberCommandHandler
    list_members: ListMembersQueryHandler
    service_keys: ServiceKeys


async def build_services(settings: Settings, resources: Resources) -> Services:
    sessions = resources.sessions
    tokens = Rs256Tokens(settings.secret("identity_jwt_private_key").get_secret_value())

    def uow() -> SqlUnitOfWork:
        return SqlUnitOfWork(sessions)

    limiter = SqlRateLimiter(sessions, utcnow)
    return Services(
        exchange=ExchangeLoginCodeCommandHandler(uow, limiter, utcnow, tokens),
        logout=LogoutCommandHandler(uow, utcnow),
        authenticate=AuthenticateSessionQueryHandler(uow, tokens, utcnow),
        cookies=CookiePolicy(
            secure=settings.environment not in _INSECURE_COOKIE_ENVIRONMENTS
        ),
        issue_code=IssueLoginCodeCommandHandler(uow, limiter, utcnow),
        find_member=FindMemberByTelegramQueryHandler(uow),
        submit_join=SubmitJoinRequestCommandHandler(uow, limiter, utcnow),
        decide=DecideMemberCommandHandler(uow, utcnow),
        list_members=ListMembersQueryHandler(uow),
        service_keys=ServiceKeys(
            {
                settings.secret("automation_service_key").get_secret_value(): (
                    "automation",
                    frozenset(
                        {
                            Operation.FIND_MEMBER,
                            Operation.ISSUE_LOGIN_CODE,
                            Operation.SUBMIT_JOIN_REQUEST,
                        }
                    ),
                )
            }
        ),
    )


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(Settings, service="identity")
    resources = providers.Resource(open_resources, settings=settings)
    # CLI processes need only the database, not RabbitMQ.
    database = providers.Resource(open_database, settings=settings)
    services = providers.Resource(
        build_services, settings=settings, resources=resources
    )
