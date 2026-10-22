"""CSRF guard for state-changing routes: the browser must name our own origin.

Same rule as Identity's: the session cookie is SameSite=Strict, and the ingress
forwards the browser's Host and the scheme it was reached by.
"""

from fastapi import HTTPException, Request


def _expected_origin(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{scheme}://{request.headers.get('host', '')}".lower()


async def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin", "").lower()
    if origin != _expected_origin(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
