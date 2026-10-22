"""CSRF guard for state-changing routes: the browser must name our own origin.

The session cookie is SameSite=Strict, and the ingress forwards the browser's
Host and the scheme it was reached by.
"""

from fastapi import Request

from kernel_platform.http.errors import ApiError


def _expected_origin(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{scheme}://{request.headers.get('host', '')}".lower()


async def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin", "").lower()
    if origin != _expected_origin(request):
        raise ApiError(
            status_code=403,
            code="origin_not_allowed",
            message="Запрос отправлен с другого источника.",
        )
