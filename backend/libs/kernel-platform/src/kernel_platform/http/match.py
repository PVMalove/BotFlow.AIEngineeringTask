"""Общее разворачивание доменного `Result` в HTTP-ответ (ADR 0002/0003)."""

from kernel_domain.result import Result

from kernel_platform.http.envelope import ApiResponse
from kernel_platform.http.errors import (
    ApiError,
    details_for_error,
    status_code_for_error_type,
)
from kernel_platform.pagination import Page, PageInfo


def _raise_for_error[T](result: Result[T]) -> None:
    if result.is_err:
        error = result.error
        raise ApiError(
            status_code=status_code_for_error_type(error.type),
            code=error.code,
            message=error.description,
            details=details_for_error(error),
        )


def match_result[T](result: Result[T]) -> ApiResponse[T]:
    """Успешный `Result` заворачивается в `ApiResponse`; неуспешный поднимает
    `ApiError`, перехватываемый `register_error_handlers` и превращаемый в
    структурированный `ErrorResponse` с сохранением `Error.code`."""
    _raise_for_error(result)
    return ApiResponse(data=result.value)


def match_created[T](result: Result[T]) -> ApiResponse[T]:
    """Семантический алиас `match_result` для create-эндпоинтов — HTTP 201
    по-прежнему задаёт только декоратор роута, не эта функция."""
    return match_result(result)


def _page_meta(page_info: PageInfo) -> dict[str, object]:
    return {
        "next_cursor": page_info.next_cursor,
        "prev_cursor": page_info.prev_cursor,
        "has_more": page_info.has_more,
        "has_prev": page_info.has_prev,
    }


def match_page[T](result: Result[Page[T]]) -> ApiResponse[list[T]]:
    """Семантический вариант `match_result` для keyset-пагинированных
    list-эндпоинтов: `Page.items` уходит в `data`, `Page.page_info` — в
    `meta` (issue #221)."""
    _raise_for_error(result)
    page = result.value
    return ApiResponse(data=page.items, meta=_page_meta(page.page_info))
