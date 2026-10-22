"""Browser-facing protections: session cookie policy and the same-origin check."""

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from fastapi import HTTPException, Request, Response

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


def _expected_origin(request: Request) -> str:
    # The ingress forwards the browser's Host and the scheme it was reached by.
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{scheme}://{request.headers.get('host', '')}".lower()


async def require_same_origin(request: Request) -> None:
    """CSRF guard for state-changing routes: the browser must name our own origin."""
    origin = request.headers.get("origin", "").lower()
    if origin != _expected_origin(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")


class Operation(StrEnum):
    """Internal operations a service key may be limited to."""

    FIND_MEMBER = "members:find"
    ISSUE_LOGIN_CODE = "login-codes:issue"


class ServiceKeys:
    """Per-caller random keys, each with an allow-list; never general admin rights."""

    def __init__(self, keys: Mapping[str, tuple[str, frozenset[Operation]]]) -> None:
        self._keys = dict(keys)

    def authorize(self, presented: str | None, operation: Operation) -> str:
        """Return the caller name, or raise 401 (unknown key) / 403 (not allowed)."""
        caller: tuple[str, frozenset[Operation]] | None = None
        for key, entry in self._keys.items():
            # Compare against every key so timing does not reveal which matched.
            if presented is not None and hmac.compare_digest(
                presented.encode(), key.encode()
            ):
                caller = entry
        if caller is None:
            raise HTTPException(status_code=401, detail="service_key_required")
        if operation not in caller[1]:
            raise HTTPException(status_code=403, detail="operation_not_allowed")
        return caller[0]
