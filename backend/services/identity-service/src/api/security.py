"""Session cookie policy and the service keys of internal callers."""

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Response
from kernel_domain.result import Result

from domain.errors import IdentityErrors

SESSION_COOKIE = "botflow_session"


@dataclass(frozen=True, slots=True)
class CookiePolicy:
    """`secure` may be False only for the loopback HTTP profile."""

    secure: bool

    def set_session(self, response: Response, token: str, max_age: int) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=max_age,
            path="/",
            httponly=True,
            secure=self.secure,
            samesite="strict",
        )

    def clear(self, response: Response) -> None:
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            httponly=True,
            secure=self.secure,
            samesite="strict",
        )


class Operation(StrEnum):
    """Internal operations a service key may be limited to."""

    FIND_MEMBER = "members:find"
    ISSUE_LOGIN_CODE = "login-codes:issue"
    SUBMIT_JOIN_REQUEST = "join-requests:submit"


class ServiceKeys:
    """Per-caller random keys, each with an allow-list; never general admin rights."""

    def __init__(self, keys: Mapping[str, tuple[str, frozenset[Operation]]]) -> None:
        self._keys = dict(keys)

    def authorize(self, presented: str | None, operation: Operation) -> Result[str]:
        """The caller name, or 401 (unknown key) / 403 (not allowed) as an error."""
        caller: tuple[str, frozenset[Operation]] | None = None
        for key, entry in self._keys.items():
            # Compare against every key so timing does not reveal which matched.
            if presented is not None and hmac.compare_digest(
                presented.encode(), key.encode()
            ):
                caller = entry
        if caller is None:
            return Result[str].fail(IdentityErrors.service_key_required())
        if operation not in caller[1]:
            return Result[str].fail(IdentityErrors.operation_not_allowed())
        return Result[str].ok(caller[0])
