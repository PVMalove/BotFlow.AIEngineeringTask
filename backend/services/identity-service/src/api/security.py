"""Browser-facing protections: session cookie policy and the same-origin check."""

from dataclasses import dataclass

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
