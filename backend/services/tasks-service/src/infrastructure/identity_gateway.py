"""Session check through Identity; exceptions never carry cookies or bodies."""

import httpx

from application.ports import IdentityUnavailable, NotApproved, Unauthenticated

SESSION_COOKIE = "botflow_session"


class HttpIdentityGateway:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def require_approved(self, token: str) -> None:
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
            status = response.json()["status"]
        except (ValueError, KeyError, TypeError):
            raise IdentityUnavailable from None
        if status != "approved":
            raise NotApproved
