"""Service-key HTTP adapter. Exceptions never contain credentials or response bodies."""

import re
from typing import Any
from uuid import UUID

import httpx
from kernel_domain.errors import Error
from kernel_domain.result import Result

from application.ports import IdentityUnavailable, TelegramMember
from domain.errors import AutomationErrors
from domain.value_objects.join_outcome import JoinOutcome


class HttpIdentityGateway:
    def __init__(self, client: httpx.AsyncClient, service_key: str) -> None:
        self._client = client
        self._headers = {"X-Service-Key": service_key}

    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        response = await self._request(
            "GET", f"/internal/v1/members/by-telegram/{telegram_id}"
        )
        if response.status_code == 404:
            return None
        try:
            body = _data(response)
            member = TelegramMember(
                telegram_id=body["telegram_id"],
                role=body["role"],
                status=body["status"],
                access_version=body["access_version"],
                member_id=UUID(body["id"]) if "id" in body else None,
            )
            if not isinstance(member.access_version, int):
                raise ValueError
            if member.telegram_id != telegram_id:
                raise ValueError
            return member
        except (ValueError, KeyError, TypeError):
            raise IdentityUnavailable from None

    async def issue_login_code(self, telegram_id: int) -> Result[str]:
        response = await self._request(
            "POST", "/internal/v1/login-codes", {"telegram_id": telegram_id}
        )
        if response.status_code != 200:
            return Result[str].fail(_refusal(response))
        try:
            code = _data(response)["code"]
            if not isinstance(code, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{32}", code
            ):
                raise ValueError
            return Result[str].ok(code)
        except (ValueError, KeyError, TypeError):
            raise IdentityUnavailable from None

    async def submit_join_request(
        self, telegram_id: int, display_name: str | None
    ) -> Result[JoinOutcome]:
        body: dict[str, object] = {"telegram_id": telegram_id}
        if display_name is not None:
            body["display_name"] = display_name
        response = await self._request("POST", "/internal/v1/join-requests", body)
        if response.status_code != 200:
            return Result[JoinOutcome].fail(_refusal(response))
        try:
            return Result[JoinOutcome].ok(JoinOutcome(_data(response)["outcome"]))
        except (ValueError, KeyError, TypeError):
            raise IdentityUnavailable from None

    async def _request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method, path, json=body, headers=self._headers
            )
        except httpx.HTTPError:
            raise IdentityUnavailable from None
        if response.status_code not in _ANSWERED:
            raise IdentityUnavailable
        return response


# 200 is the answer, 404 an unknown member; 403 and 429 are refusals Identity explains.
_ANSWERED = {200, 403, 404, 429}


def _data(response: httpx.Response) -> dict[str, Any]:
    data: dict[str, Any] = response.json()["data"]
    return data


def _refusal(response: httpx.Response) -> Error:
    """The member may not log in, or must wait; any other refusal is not theirs."""
    if response.status_code == 429:
        return AutomationErrors.rate_limited()
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        raise IdentityUnavailable from None
    if code == "not_approved":
        return AutomationErrors.login_denied()
    raise IdentityUnavailable  # e.g. the service key may not do this
