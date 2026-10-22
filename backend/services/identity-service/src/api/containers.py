"""Service composition root; each process owns its infrastructure lifecycle."""

from dataclasses import dataclass
from datetime import UTC, datetime

from botflow_platform.resources import Resources, open_database, open_resources
from botflow_platform.settings import Settings
from dependency_injector import containers, providers

from api.security import CookiePolicy, Operation, ServiceKeys
from application.commands.access import DecideMember
from application.commands.auth import ExchangeLoginCode, IssueLoginCode, Logout
from application.commands.join import SubmitJoinRequest
from application.queries.members import (
    AuthenticateSession,
    FindMemberByTelegram,
    ListMembers,
)
from infrastructure.db.unit_of_work import SqlRateLimiter, SqlUnitOfWork
from infrastructure.security.jwt import Rs256Tokens

# Only the loopback HTTP profiles may use a cookie without the Secure attribute.
_INSECURE_COOKIE_ENVIRONMENTS = {"local", "test"}


@dataclass(frozen=True, slots=True)
class Services:
    """Use cases the HTTP layer needs, wired to the process resources."""

    exchange: ExchangeLoginCode
    logout: Logout
    authenticate: AuthenticateSession
    cookies: CookiePolicy
    issue_code: IssueLoginCode
    find_member: FindMemberByTelegram
    submit_join: SubmitJoinRequest
    decide: DecideMember
    list_members: ListMembers
    service_keys: ServiceKeys


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def build_services(settings: Settings, resources: Resources) -> Services:
    sessions = resources.sessions
    tokens = Rs256Tokens(settings.secret("identity_jwt_private_key").get_secret_value())

    def uow() -> SqlUnitOfWork:
        return SqlUnitOfWork(sessions)

    limiter = SqlRateLimiter(sessions, _utcnow)
    return Services(
        exchange=ExchangeLoginCode(uow, limiter, _utcnow, tokens),
        logout=Logout(uow, _utcnow),
        authenticate=AuthenticateSession(uow, tokens, _utcnow),
        cookies=CookiePolicy(
            secure=settings.environment not in _INSECURE_COOKIE_ENVIRONMENTS
        ),
        issue_code=IssueLoginCode(uow, limiter, _utcnow),
        find_member=FindMemberByTelegram(uow),
        submit_join=SubmitJoinRequest(uow, limiter, _utcnow),
        decide=DecideMember(uow, _utcnow),
        list_members=ListMembers(uow),
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
