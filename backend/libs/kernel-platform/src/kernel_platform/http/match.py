"""Unwrapping a domain `Result` into an HTTP response."""

from typing import NoReturn

from kernel_domain.errors import Error, ErrorType
from kernel_domain.result import Result

from kernel_platform.http.envelope import ApiResponse
from kernel_platform.http.errors import (
    ApiError,
    details_for_error,
    status_code_for_error_type,
)
from kernel_platform.pagination import Page, PageInfo


def unwrap[T](result: Result[T]) -> T:
    """The value of a successful `Result`; a failed one raises `ApiError`.

    For FastAPI dependencies (authentication), where the value is needed before
    the endpoint runs and there is no response envelope to build."""
    if result.is_err:
        raise_error(result.error)
    return result.value


def raise_error(error: Error) -> NoReturn:
    """Raise `error` as the `ApiError` of its type."""
    raise ApiError(
        status_code=status_code_for_error_type(error.type),
        code=error.code,
        message=error.description,
        details=details_for_error(error),
        headers=(
            {"Retry-After": "60"} if error.type is ErrorType.RATE_LIMITED else None
        ),
    )


def match_result[T](result: Result[T]) -> ApiResponse[T]:
    """A successful `Result` becomes `ApiResponse`; a failed one raises `ApiError`,
    which `register_error_handlers` turns into the error envelope."""
    return ApiResponse(data=unwrap(result))


def match_page[T](result: Result[Page[T]]) -> ApiResponse[list[T]]:
    """`Page.items` go to `data`, `Page.page_info` to `meta`."""
    page = unwrap(result)
    return ApiResponse(data=page.items, meta=_page_meta(page.page_info))


def _page_meta(page_info: PageInfo) -> dict[str, object]:
    return {"next_cursor": page_info.next_cursor, "has_more": page_info.has_more}
