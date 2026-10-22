"""Session check through Identity; exceptions never carry cookies or bodies."""

from uuid import UUID

import httpx

from application.ports import IdentityUnavailable, NotApproved, Unauthenticated
from domain.tasks import Actor, Role

SESSION_COOKIE = "botflow_session"


class HttpIdentityGateway:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def require_approved(self, token: str) -> Actor:
        try:
            response = await self._client.get(
                "/api/v1/users/me", headers={"Cookie": f"{SESSION_COOKIE}={token}"}
            )
        except httpx.HTTPError:
            raise IdentityUnavailable from None
        if response.status_code == 401:
            raise Unauthenticated
        if response.status_code == 403:
            raise NotApproved
        if response.status_code != 200:
            raise IdentityUnavailable
        try:
            member = response.json()
            status = member["status"]
            actor = Actor(UUID(member["id"]), Role(member["role"]))
        except (ValueError, KeyError, TypeError):
            raise IdentityUnavailable from None
        if status != "approved":
            raise NotApproved
        return actor
