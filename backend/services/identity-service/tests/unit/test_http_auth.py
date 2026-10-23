"""Cookie session HTTP contract of the Identity API (public routes)."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
import pytest
from conftest import Clock, FakeRateLimiter, FakeUnitOfWork, Store, with_status
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.exc import OperationalError, TimeoutError

from api.containers import Services
from api.dependencies import get_services
from api.main import create_identity_app
from api.security import CookiePolicy, Operation, ServiceKeys
from application.commands.access import DecideMemberCommandHandler
from application.commands.auth import (
    EnsureInitialAdminCommand,
    EnsureInitialAdminCommandHandler,
    ExchangeLoginCodeCommandHandler,
    IssueLoginCodeCommand,
    IssueLoginCodeCommandHandler,
    LogoutCommandHandler,
)
from application.commands.join import SubmitJoinRequestCommandHandler
from application.queries.members import (
    AuthenticateSessionQueryHandler,
    FindMemberByTelegramQueryHandler,
    ListMembersQueryHandler,
)
from domain.value_objects.member_status import MemberStatus
from infrastructure.security.jwt import Rs256Tokens

ADMIN = 4_200_000_001
ORIGIN = {"Origin": "http://test"}
SERVICE_KEY = {"X-Service-Key": "automation-key-for-tests"}


class Env:
    def __init__(self, secure: bool) -> None:
        self.store = Store()
        self.clock = Clock()
        self.clock.current = datetime.now(UTC)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        tokens = Rs256Tokens(self.pem)
        limiter = FakeRateLimiter()

        def uow() -> FakeUnitOfWork:
            return FakeUnitOfWork(self.store)

        self.issue = IssueLoginCodeCommandHandler(uow, limiter, self.clock.now)
        self.services = Services(
            exchange=ExchangeLoginCodeCommandHandler(
                uow, limiter, self.clock.now, tokens
            ),
            logout=LogoutCommandHandler(uow, self.clock.now),
            authenticate=AuthenticateSessionQueryHandler(uow, tokens, self.clock.now),
            cookies=CookiePolicy(secure=secure),
            issue_code=self.issue,
            find_member=FindMemberByTelegramQueryHandler(uow),
            submit_join=SubmitJoinRequestCommandHandler(uow, limiter, self.clock.now),
            decide=DecideMemberCommandHandler(uow, self.clock.now),
            list_members=ListMembersQueryHandler(uow),
            service_keys=ServiceKeys(
                {
                    "automation-key-for-tests": (
                        "automation",
                        frozenset(Operation),
                    ),
                    "key-without-operations": ("restricted", frozenset()),
                }
            ),
        )

    async def login_code(self) -> str:
        admin_uow = EnsureInitialAdminCommandHandler(
            lambda: FakeUnitOfWork(self.store), self.clock.now
        )
        await admin_uow.execute(EnsureInitialAdminCommand(ADMIN))
        return (await self.issue.execute(IssueLoginCodeCommand(ADMIN))).value.code


@pytest.fixture
def env() -> Env:
    return Env(secure=False)


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


async def client_for(env: Env) -> AsyncIterator[httpx.AsyncClient]:
    app = create_identity_app()
    app.dependency_overrides[get_services] = lambda: env.services
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def client(env: Env) -> AsyncIterator[httpx.AsyncClient]:
    async for value in client_for(env):
        yield value


async def login(client: httpx.AsyncClient, env: Env) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/exchange", json={"code": await env.login_code()}, headers=ORIGIN
    )


async def test_exchange_sets_an_http_only_8_hour_session_cookie(
    client: httpx.AsyncClient, env: Env
) -> None:
    response = await login(client, env)

    assert response.status_code == 200
    assert response.json()["data"]["telegram_id"] == ADMIN
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=28800" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie  # loopback HTTP profile only
    token = response.cookies["botflow_session"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 8 * 3600
    assert token not in response.text


async def test_secure_flag_is_set_outside_the_local_profile() -> None:
    env = Env(secure=True)
    async for client in client_for(env):
        response = await login(client, env)

    assert "Secure" in response.headers["set-cookie"]


async def test_wrong_code_gives_401_and_no_cookie(
    client: httpx.AsyncClient, env: Env
) -> None:
    await env.login_code()

    response = await client.post(
        "/api/v1/auth/exchange", json={"code": "x" * 32}, headers=ORIGIN
    )

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert not env.store.sessions


async def test_replayed_code_gives_401(client: httpx.AsyncClient, env: Env) -> None:
    code = await env.login_code()
    body = {"code": code}
    await client.post("/api/v1/auth/exchange", json=body, headers=ORIGIN)

    replay = await client.post("/api/v1/auth/exchange", json=body, headers=ORIGIN)

    assert replay.status_code == 401


async def test_exchange_is_rate_limited_with_429(
    client: httpx.AsyncClient, env: Env
) -> None:
    for _ in range(30):
        await client.post(
            "/api/v1/auth/exchange", json={"code": "x" * 32}, headers=ORIGIN
        )

    response = await client.post(
        "/api/v1/auth/exchange", json={"code": "x" * 32}, headers=ORIGIN
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://evil.example"}, {"Origin": "null"}],
    ids=["missing", "foreign", "opaque"],
)
async def test_state_changing_requests_require_the_same_origin(
    client: httpx.AsyncClient, env: Env, headers: dict[str, str]
) -> None:
    code = await env.login_code()

    response = await client.post(
        "/api/v1/auth/exchange", json={"code": code}, headers=headers
    )

    assert response.status_code == 403
    assert not env.store.sessions  # the code was not even tried


async def test_forwarded_scheme_is_part_of_the_expected_origin(
    client: httpx.AsyncClient, env: Env
) -> None:
    code = await env.login_code()

    response = await client.post(
        "/api/v1/auth/exchange",
        json={"code": code},
        headers={"Origin": "https://test", "X-Forwarded-Proto": "https"},
    )

    assert response.status_code == 200


async def test_me_returns_the_current_member(
    client: httpx.AsyncClient, env: Env
) -> None:
    await login(client, env)

    response = await client.get("/api/v1/users/me")

    assert response.status_code == 200
    assert response.json()["data"]["telegram_id"] == ADMIN
    assert response.json()["data"]["role"] == "admin"
    assert response.headers["cache-control"] == "no-store"


async def test_me_without_cookie_is_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/users/me")).status_code == 401


async def test_me_rejects_a_forged_token(client: httpx.AsyncClient, env: Env) -> None:
    await login(client, env)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(
        client.cookies["botflow_session"], options={"verify_signature": False}
    )
    forged = jwt.encode(claims, other, algorithm="RS256")

    response = await client.get(
        "/api/v1/users/me", headers={"Cookie": f"botflow_session={forged}"}
    )

    assert response.status_code == 401


async def test_me_rechecks_access_on_every_request(
    client: httpx.AsyncClient, env: Env
) -> None:
    await login(client, env)
    (member,) = env.store.members.values()
    env.store.members[member.id] = with_status(member, MemberStatus.BLOCKED)

    assert (await client.get("/api/v1/users/me")).status_code == 401


async def test_logout_revokes_the_session_and_clears_the_cookie(
    client: httpx.AsyncClient, env: Env
) -> None:
    await login(client, env)
    token = client.cookies["botflow_session"]

    response = await client.post("/api/v1/auth/logout", headers=ORIGIN)

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    replay = await client.get(
        "/api/v1/users/me", headers={"Cookie": f"botflow_session={token}"}
    )
    assert replay.status_code == 401


async def test_logout_from_a_foreign_origin_keeps_the_session(
    client: httpx.AsyncClient, env: Env
) -> None:
    await login(client, env)

    response = await client.post(
        "/api/v1/auth/logout", headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    assert (await client.get("/api/v1/users/me")).status_code == 200


@pytest.mark.parametrize(
    "failure",
    [
        OperationalError("SELECT 1", {}, OSError("connection refused")),
        ConnectionRefusedError(),
        TimeoutError("connection pool exhausted"),
    ],
    ids=["database", "network", "pool-timeout"],
)
async def test_unavailable_storage_gives_503_not_access(
    env: Env, failure: Exception
) -> None:
    class UnavailableAuth:
        async def execute(self, token: str) -> Any:
            raise failure

    env.services = replace(
        env.services,
        authenticate=UnavailableAuth(),  # type: ignore[arg-type]
    )
    app = create_identity_app()
    app.dependency_overrides[get_services] = lambda: env.services
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/users/me", headers={"Cookie": "botflow_session=anything"}
        )

    assert response.status_code == 503
    assert error_code(response) == "service_unavailable"
    assert response.headers["retry-after"] == "5"
    assert "set-cookie" not in response.headers


async def test_internal_lookup_returns_the_member_for_a_service_key(
    client: httpx.AsyncClient, env: Env
) -> None:
    await env.login_code()

    response = await client.get(
        f"/internal/v1/members/by-telegram/{ADMIN}", headers=SERVICE_KEY
    )

    assert response.status_code == 200
    assert response.json()["data"]["role"] == "admin"
    assert response.json()["data"]["status"] == "approved"


async def test_internal_lookup_of_unknown_telegram_id_is_404(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/internal/v1/members/by-telegram/7", headers=SERVICE_KEY
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"X-Service-Key": "wrong"}, 401),
        ({"X-Service-Key": "key-without-operations"}, 403),
    ],
    ids=["missing", "wrong", "operation-not-allowed"],
)
async def test_internal_routes_demand_a_key_that_allows_the_operation(
    client: httpx.AsyncClient, env: Env, headers: dict[str, str], status: int
) -> None:
    await env.login_code()

    lookup = await client.get(
        f"/internal/v1/members/by-telegram/{ADMIN}", headers=headers
    )
    issue = await client.post(
        "/internal/v1/login-codes", json={"telegram_id": ADMIN}, headers=headers
    )

    assert (lookup.status_code, issue.status_code) == (status, status)


async def test_a_browser_session_does_not_open_internal_routes(
    client: httpx.AsyncClient, env: Env
) -> None:
    await login(client, env)

    response = await client.get(f"/internal/v1/members/by-telegram/{ADMIN}")

    assert response.status_code == 401


async def test_internal_issue_gives_a_code_the_browser_can_exchange(
    client: httpx.AsyncClient, env: Env
) -> None:
    await env.login_code()

    issued = await client.post(
        "/internal/v1/login-codes", json={"telegram_id": ADMIN}, headers=SERVICE_KEY
    )
    exchanged = await client.post(
        "/api/v1/auth/exchange",
        json={"code": issued.json()["data"]["code"]},
        headers=ORIGIN,
    )

    assert issued.status_code == 200
    assert len(issued.json()["data"]["code"]) == 32
    assert issued.headers["cache-control"] == "no-store"
    assert exchanged.status_code == 200


async def test_internal_issue_refuses_an_identity_without_approval(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/internal/v1/login-codes", json={"telegram_id": 99}, headers=SERVICE_KEY
    )

    assert response.status_code == 403


async def test_internal_issue_is_rate_limited_with_429(
    client: httpx.AsyncClient, env: Env
) -> None:
    await env.login_code()  # one issued directly, four more through HTTP
    for _ in range(4):
        await client.post(
            "/internal/v1/login-codes", json={"telegram_id": ADMIN}, headers=SERVICE_KEY
        )

    response = await client.post(
        "/internal/v1/login-codes", json={"telegram_id": ADMIN}, headers=SERVICE_KEY
    )

    assert response.status_code == 429


async def test_internal_issue_rejects_a_malformed_body(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/internal/v1/login-codes", json={"telegram_id": "abc"}, headers=SERVICE_KEY
    )

    assert response.status_code == 422


async def test_internal_routes_report_unavailable_storage_as_503(env: Env) -> None:
    class UnavailableMember:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise OperationalError("SELECT 1", {}, OSError("down"))

    env.services = replace(
        env.services,
        find_member=UnavailableMember(),  # type: ignore[arg-type]
    )
    async for client in client_for(env):
        response = await client.get(
            f"/internal/v1/members/by-telegram/{ADMIN}", headers=SERVICE_KEY
        )

    assert response.status_code == 503


async def test_internal_join_creates_a_pending_member_without_access(
    client: httpx.AsyncClient, env: Env
) -> None:
    response = await client.post(
        "/internal/v1/join-requests",
        json={"telegram_id": 7_000_000_001, "display_name": "  Ann  "},
        headers=SERVICE_KEY,
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["outcome"] == "submitted"
    assert body["member"]["status"] == "pending"
    assert body["member"]["role"] == "user"
    assert body["member"]["access_version"] == 1
    assert response.headers["cache-control"] == "no-store"
    (member,) = env.store.members.values()
    assert member.display_name == "Ann"
    code = await client.post(
        "/internal/v1/login-codes",
        json={"telegram_id": 7_000_000_001},
        headers=SERVICE_KEY,
    )
    assert code.status_code == 403  # pending is not approved


async def test_internal_join_is_idempotent_and_never_unblocks(
    client: httpx.AsyncClient, env: Env
) -> None:
    join = {"telegram_id": 7_000_000_001}
    await client.post("/internal/v1/join-requests", json=join, headers=SERVICE_KEY)
    again = await client.post(
        "/internal/v1/join-requests", json=join, headers=SERVICE_KEY
    )
    (member,) = env.store.members.values()
    env.store.members[member.id] = with_status(member, MemberStatus.BLOCKED)
    blocked = await client.post(
        "/internal/v1/join-requests", json=join, headers=SERVICE_KEY
    )

    assert again.json()["data"]["outcome"] == "already_pending"
    assert blocked.json()["data"]["outcome"] == "blocked"
    assert blocked.json()["data"]["member"]["status"] == "blocked"


async def test_internal_join_demands_its_own_operation(
    client: httpx.AsyncClient,
) -> None:
    body = {"telegram_id": 7_000_000_001}
    no_key = await client.post("/internal/v1/join-requests", json=body)
    restricted = await client.post(
        "/internal/v1/join-requests",
        json=body,
        headers={"X-Service-Key": "key-without-operations"},
    )

    assert no_key.status_code == 401
    assert restricted.status_code == 403


async def test_internal_join_is_rate_limited_with_429(
    client: httpx.AsyncClient,
) -> None:
    statuses = [
        (
            await client.post(
                "/internal/v1/join-requests",
                json={"telegram_id": 7_000_000_001},
                headers=SERVICE_KEY,
            )
        ).status_code
        for _ in range(6)
    ]

    assert statuses == [200] * 5 + [429]


@pytest.mark.parametrize(
    "body", [{"telegram_id": 0}, {"telegram_id": 1, "display_name": "x" * 65}]
)
async def test_internal_join_rejects_a_malformed_body(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    response = await client.post(
        "/internal/v1/join-requests", json=body, headers=SERVICE_KEY
    )

    assert response.status_code == 422
