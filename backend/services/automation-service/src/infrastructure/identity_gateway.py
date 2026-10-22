"""Service-key HTTP adapter. Exceptions never contain credentials or response bodies."""

import re

import httpx

from application.ports import (
    IdentityUnavailable,
    JoinOutcome,
    LoginDenied,
    LoginRateLimited,
    TelegramMember,
)


class HttpIdentityGateway:
    def __init__(self, client: httpx.AsyncClient, service_key: str) -> None:
        self._client = client
        self._headers = {"X-Service-Key": service_key}

    async def _request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method, path, json=body, headers=self._headers
            )
        except httpx.HTTPError:
            raise IdentityUnavailable from None
        if response.status_code == 429:
            raise LoginRateLimited
        if response.status_code == 403:
            raise LoginDenied
        if response.status_code not in {200, 404}:
            raise IdentityUnavailable
        return response

    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        response = await self._request(
            "GET", f"/internal/v1/members/by-telegram/{telegram_id}"
        )
        if response.status_code == 404:
            return None
        try:
            body = response.json()
            member = TelegramMember(
                telegram_id=body["telegram_id"],
                role=body["role"],
                status=body["status"],
            )
            if member.telegram_id != telegram_id:
                raise ValueError
            return member
        except (ValueError, KeyError, TypeError):
            raise IdentityUnavailable from None

    async def issue_login_code(self, telegram_id: int) -> str:
        response = await self._request(
            "POST", "/internal/v1/login-codes", {"telegram_id": telegram_id}
        )
        try:
            code = response.json()["code"]
            if not isinstance(code, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{32}", code
            ):
                raise ValueError
            return code
        except (ValueError, KeyError, TypeError):
            raise IdentityUnavailable from None

    async def submit_join_request(
        self, telegram_id: int, display_name: str | None
    ) -> JoinOutcome:
        body: dict[str, object] = {"telegram_id": telegram_id}
        if display_name is not None:
            body["display_name"] = display_name
        response = await self._request("POST", "/internal/v1/join-requests", body)
        try:
            return JoinOutcome(response.json()["outcome"])
        except (ValueError, KeyError, TypeError):
            raise IdentityUnavailable from None
