"""Cookie session HTTP contract of the Identity API (public routes)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
import pytest
from conftest import Clock, FakeRateLimiter, FakeUnitOfWork, Store
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.exc import OperationalError

from api.containers import Services
from api.main import create_identity_app
from api.routes import get_services
from api.security import CookiePolicy
from application.commands.auth import (
    EnsureInitialAdmin,
    ExchangeLoginCode,
    IssueLoginCode,
    Logout,
)
from application.queries.members import AuthenticateSession
from domain.entities.auth import MemberStatus
from infrastructure.security.jwt import Rs256Tokens

ADMIN = 4_200_000_001
ORIGIN = {"Origin": "http://test"}


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

        self.issue = IssueLoginCode(uow, limiter, self.clock)
        self.services = Services(
            exchange=ExchangeLoginCode(uow, limiter, self.clock, tokens),
            logout=Logout(uow, self.clock),
            authenticate=AuthenticateSession(uow, tokens, self.clock),
            cookies=CookiePolicy(secure=secure),
        )

    async def login_code(self) -> str:
        await EnsureInitialAdmin(lambda: FakeUnitOfWork(self.store), self.clock)(ADMIN)
        return (await self.issue(ADMIN)).code


@pytest.fixture
def env() -> Env:
    return Env(secure=False)


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
    assert response.json()["telegram_id"] == ADMIN
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
    assert response.json()["telegram_id"] == ADMIN
    assert response.json()["role"] == "admin"
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
    env.store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

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
    ],
    ids=["database", "network"],
)
async def test_unavailable_storage_gives_503_not_access(
    env: Env, failure: Exception
) -> None:
    async def unavailable(token: str) -> Any:
        raise failure

    env.services = Services(
        exchange=env.services.exchange,
        logout=env.services.logout,
        authenticate=unavailable,  # type: ignore[arg-type]
        cookies=env.services.cookies,
    )
    async for client in client_for(env):
        response = await client.get(
            "/api/v1/users/me", headers={"Cookie": "botflow_session=anything"}
        )

    assert response.status_code == 503
