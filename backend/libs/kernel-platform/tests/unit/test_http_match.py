"""ADR 0002/0003: общий конверт успеха + разворачивание `Result` в HTTP."""

import pytest
from kernel_domain.errors import Error, ErrorList, ErrorType
from kernel_domain.result import Result

from kernel_platform.http.envelope import ApiResponse
from kernel_platform.http.errors import ApiError, ErrorDetail
from kernel_platform.http.match import match_created, match_page, match_result
from kernel_platform.pagination import Page, PageInfo


def test_api_response_defaults_meta_to_an_empty_object() -> None:
    response = ApiResponse[int](data=1)

    assert response.data == 1
    assert response.meta == {}


def test_match_result_wraps_an_ok_result_in_the_envelope() -> None:
    result: Result[int] = Result[int].ok(42)

    response = match_result(result)

    assert response == ApiResponse[int](data=42, meta={})


def test_match_result_raises_api_error_with_the_mapped_status_and_domain_code() -> None:
    error = Error(
        code="invalid_name", description="Плохое имя", type=ErrorType.VALIDATION
    )
    result: Result[int] = Result[int].fail(error)

    with pytest.raises(ApiError) as exc_info:
        match_result(result)

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "invalid_name"
    assert exc_info.value.message == "Плохое имя"
    assert exc_info.value.details is None


def test_match_result_attaches_details_for_an_error_list() -> None:
    error_list = ErrorList.of(
        [
            Error.validation("invalid_name", "Плохое имя", invalid_field="name"),
            Error.validation("invalid_price", "Плохая цена", invalid_field="price"),
        ]
    )
    result: Result[int] = Result[int].fail(error_list)

    with pytest.raises(ApiError) as exc_info:
        match_result(result)

    assert exc_info.value.code == "general_multiple_validation_errors"
    assert exc_info.value.details == [
        ErrorDetail(field="name", issue="Плохое имя"),
        ErrorDetail(field="price", issue="Плохая цена"),
    ]


def test_match_created_behaves_exactly_like_match_result() -> None:
    ok: Result[str] = Result[str].ok("created")
    assert match_created(ok) == match_result(Result[str].ok("created"))

    error = Error(
        code="already_active", description="Конфликт", type=ErrorType.CONFLICT
    )
    with pytest.raises(ApiError) as exc_info:
        match_created(Result[str].fail(error))
    assert exc_info.value.status_code == 409


def test_match_page_splits_items_into_data_and_page_info_into_meta() -> None:
    page_info = PageInfo(
        next_cursor="n", prev_cursor=None, has_more=True, has_prev=False
    )
    result: Result[Page[int]] = Result[Page[int]].ok(
        Page(items=[1, 2], page_info=page_info)
    )

    response = match_page(result)

    assert response == ApiResponse[list[int]](
        data=[1, 2],
        meta={
            "next_cursor": "n",
            "prev_cursor": None,
            "has_more": True,
            "has_prev": False,
        },
    )


def test_match_page_raises_api_error_with_the_mapped_status_and_domain_code() -> None:
    error = Error(
        code="invalid_cursor",
        description="Некорректный курсор",
        type=ErrorType.VALIDATION,
    )
    result: Result[Page[int]] = Result[Page[int]].fail(error)

    with pytest.raises(ApiError) as exc_info:
        match_page(result)

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "invalid_cursor"
