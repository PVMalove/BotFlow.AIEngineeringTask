"""Session check through Identity; exceptions never carry cookies or bodies."""

from uuid import UUID

import httpx
from kernel_domain.result import Result
from kernel_platform.security import Actor, ActorRole, AuthErrors

from application.ports import IdentityUnavailable

SESSION_COOKIE = "botflow_session"


class HttpIdentityGateway:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def require_approved(self, token: str) -> Result[Actor]:
        try:
            response = await self._client.get(
                "/api/v1/users/me", headers={"Cookie": f"{SESSION_COOKIE}={token}"}
            )
        except httpx.HTTPError:
            raise IdentityUnavailable from None
        if response.status_code == 401:
            return Result[Actor].fail(AuthErrors.unauthenticated())
        if response.status_code == 403:
            return Result[Actor].fail(AuthErrors.not_approved())
        if response.status_code != 200:
            raise IdentityUnavailable
        try:
            member = response.json()
            status = member["status"]
            actor = Actor(UUID(member["id"]), ActorRole(member["role"]))
        except (ValueError, KeyError, TypeError):
            raise IdentityUnavailable from None
        if status != "approved":
            return Result[Actor].fail(AuthErrors.not_approved())
        return Result[Actor].ok(actor)
